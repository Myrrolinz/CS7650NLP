import logging
import os
import os.path
from random import choice
import torch
import cv2
import numpy as np

logger = logging.getLogger(__name__)

class PoseDataset(torch.utils.data.Dataset):
    def __init__(self, cfg, is_train, transform=None, target_generator=None):
        super(PoseDataset, self).__init__()
        self.root = cfg.DATASET.ROOT
        self.dataset = cfg.DATASET.DATASET
        if self.dataset == 'crowdpose':
            from crowdposetools.coco import COCO
        else:
            from pycocotools.coco import COCO
        self.image_set = cfg.DATASET.TRAIN if is_train else cfg.DATASET.TEST
        self.is_train = is_train
        self.transform = transform
        self.coco = COCO(os.path.join(self.root, 'annotations', '{}_{}.json'.format(self.dataset, self.image_set)))
        self.ids = list(self.coco.imgs.keys())

        if is_train:
            if self.dataset == 'coco':
                self.filter_for_annotations()
            else:
                self.ids = [img_id for img_id in self.ids if len(self.coco.getAnnIds(imgIds=img_id, iscrowd=None)) > 0]
            self.num_keypoints = cfg.DATASET.NUM_KEYPOINTS
            self.output_size = cfg.DATASET.OUTPUT_SIZE
            self.heatmap_generator = target_generator

    def _get_image_path(self, file_name):
        images_dir = os.path.join(self.root, 'images')
        if self.dataset == 'coco': images_dir = os.path.join(images_dir, '{}2017'.format(self.image_set))
        return os.path.join(images_dir, file_name)

    def filter_for_annotations(self, min_kp_anns=1):
        print('filter for annotations (min kp=%d) ...', min_kp_anns)

        def filter_image(image_id):
            ann_ids = self.coco.getAnnIds(imgIds=image_id)
            anns = self.coco.loadAnns(ann_ids)
            anns = [ann for ann in anns if not ann.get('iscrowd')]
            if not anns:
                return False
            kp_anns = [ann for ann in anns
                       if 'keypoints' in ann and any(v > 0.0 for v in ann['keypoints'][2::3])]
            return len(kp_anns) >= min_kp_anns

        self.ids = [image_id for image_id in self.ids if filter_image(image_id)]

    def __getitem__(self, index):
        coco = self.coco
        img_id = self.ids[index]
        file_name = coco.loadImgs(img_id)[0]['file_name']
        img = cv2.imread(
            self._get_image_path(file_name),
            cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION
        )
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        results = {}
        if self.is_train:
            ann_ids = coco.getAnnIds(imgIds=img_id)
            target = coco.loadAnns(ann_ids)
            anno = [obj for obj in target]
            img_info = self.coco.loadImgs(img_id)[0]
            # mask
            m = np.zeros((img_info['height'], img_info['width']))
            if self.dataset == 'coco':
                import pycocotools
                for obj in anno:
                    if obj['iscrowd']:
                        rle = pycocotools.mask.frPyObjects(
                            obj['segmentation'], img_info['height'], img_info['width'])
                        m += pycocotools.mask.decode(rle)
                    elif obj['num_keypoints'] == 0:
                        rles = pycocotools.mask.frPyObjects(
                            obj['segmentation'], img_info['height'], img_info['width'])
                        for rle in rles:
                            m += pycocotools.mask.decode(rle)
            mask = m < 0.5

            anno = [obj for obj in anno if obj['iscrowd'] == 0 and obj['num_keypoints'] > 0]
            num_people = len(anno)
            area = np.zeros((num_people, 1))
            bboxs = np.zeros((num_people, 4, 2))
            keypoints = np.zeros((num_people, self.num_keypoints, 3))
            centers = np.zeros((num_people, 1, 3))

            for i, obj in enumerate(anno):
                keypoints[i, :, :3] = np.array(obj['keypoints']).reshape([-1, 3])
                area[i, 0] = obj['bbox'][2] * obj['bbox'][3]
                bboxs[i, :, 0], bboxs[i, :, 1] = obj['bbox'][0], obj['bbox'][1]
                bboxs[i, 1, 0] += obj['bbox'][2]
                bboxs[i, 2, 1] += obj['bbox'][3]
                bboxs[i, 3, 0] += obj['bbox'][2]; bboxs[i, 3, 1] += obj['bbox'][3]

            if self.transform:
                img, mask, keypoints_t, area, bboxs = self.transform(img, mask, keypoints, area, bboxs)

            for i, obj in enumerate(anno):
                if not self.dataset == 'crowdpose':
                    if area[i, 0] < 32 ** 2:
                        centers[i, :, 2] = 0
                        continue
                vis = (keypoints_t[i, :, 2:3] > 0).astype(np.float32)
                keypoints_sum = np.sum(keypoints_t[i, :, :2] * vis, axis=0)
                num_vis_keypoints = len(np.nonzero(keypoints_t[i, :, 2])[0])
                if num_vis_keypoints <= 0: centers[i, 0, 2] = 0; continue
                centers[i, 0, :2] = keypoints_sum / num_vis_keypoints
                centers[i, 0, 2] = 2

            keypoints_with_centers = np.concatenate((keypoints_t, centers), axis=1)

            heatmap_with_centers, _ = self.heatmap_generator(keypoints_with_centers, bboxs)

            inst_coords, inst_heatmaps, inst_masks, keypoints_coords = self.get_inst_annos(centers, keypoints_t, area, bboxs)
            if len(inst_coords) > 0:
                inst_coords = np.concatenate(inst_coords, axis=0)
                inst_heatmaps = np.concatenate(inst_heatmaps, axis=0)
                inst_masks = np.concatenate(inst_masks, axis=0)
                results['instance_coord'] = torch.from_numpy(inst_coords)
                results['instance_heatmap'] = torch.from_numpy(inst_heatmaps)
                results['instance_mask'] = torch.from_numpy(inst_masks)
                results['keypoint_coord'] = torch.from_numpy(np.array(keypoints_coords))
            results['image'] = img
            results['multi_heatmap'] = torch.from_numpy(heatmap_with_centers)
            results['multi_mask'] = torch.from_numpy(mask[None, :, :])
        else:
            results['image'] = torch.from_numpy(img)
            results['image_id'] = img_id

        return results

    def __len__(self):
        return len(self.ids)

    def get_inst_annos(self, centers, keypoints, area, bbox):
        ind_vis = []
        area_idx = np.argsort(area.squeeze())
        inst_coords, inst_heatmaps, inst_masks, keypoints_coords = [], [], [], []
        for i in area_idx:
            inst_coord = []
            center = centers[i, 0]
            if center[2] < 1: continue
            x, y = int(center[0]), int(center[1])
            if x < 0 or x >= self.output_size or y < 0 or y >= self.output_size: continue
            # rand center point in 3x3 grid
            new_x = x + choice([-1, 0, 1]) 
            new_y = y + choice([-1, 0, 1])                    
            if new_x < 0 or new_x >= self.output_size or new_y < 0 or new_y >= self.output_size:       
                new_x = x                        
                new_y = y                    
            x, y = new_x, new_y
            
            if [y, x] in ind_vis: continue
            inst_coord.append([y, x])
            ind_vis.append([y, x])
            inst_coords.append(np.array(inst_coord))
            inst_heatmap, inst_mask = self.heatmap_generator(keypoints[i:i+1, :, :], bbox[i:i+1, :, :])
            inst_heatmaps.append(inst_heatmap[None, :, :, :])
            inst_masks.append(inst_mask[None, :, :, :])
            
            keypoint_coord = []
            keypoint = keypoints[i:i+1, :, :][0]
            for j in range(len(keypoint)):   
                if keypoint[j][2] < 1: 
                    keypoint_coord.append([-1, -1])
                    continue
                kx, ky = int(keypoint[j][0]), int(keypoint[j][1])
                if kx < 0 or kx >= self.output_size or ky < 0 or ky >= self.output_size:
                    keypoint_coord.append([-1, -1])
                    continue
                keypoint_coord.append([ky, kx])
            keypoints_coords.append(keypoint_coord)
        
        return inst_coords, inst_heatmaps, inst_masks, keypoints_coords

    def __repr__(self):
        fmt_str = 'Dataset ' + self.__class__.__name__ + '\n'
        fmt_str += '    Number of datapoints: {}\n'.format(self.__len__())
        fmt_str += '    Root Location: {}'.format(self.root)
        return fmt_str