import torch

from .dataset import PoseDataset
from . import transforms as T
from .target_generator import HeatmapGenerator

def make_train_dataloader(cfg, distributed=False):
    images_per_batch = cfg.TRAIN.IMAGES_PER_GPU
    shuffle = True

    transforms = build_transforms(cfg)
    target_generator = HeatmapGenerator(cfg.DATASET.OUTPUT_SIZE)
    dataset = PoseDataset(cfg, is_train=True, transform=transforms, target_generator=target_generator)

    if distributed:
        train_sampler = torch.utils.data.distributed.DistributedSampler(dataset)
        shuffle = False
    else:
        train_sampler = None

    data_loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=images_per_batch,
        shuffle=shuffle,
        num_workers=cfg.WORKERS,
        pin_memory=cfg.PIN_MEMORY,
        sampler=train_sampler,
        collate_fn=trivial_batch_collator
    )

    return data_loader

def trivial_batch_collator(batch):
    """
    A batch collator that does nothing.
    """
    return batch

def make_test_dataloader(cfg):
    dataset = PoseDataset(cfg, is_train=False)
    data_loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=1,
        shuffle=False,
        num_workers=0,
        pin_memory=False
    )
    return dataset, data_loader

def build_transforms(cfg):
    max_rotation = cfg.DATASET.MAX_ROTATION
    min_scale = cfg.DATASET.MIN_SCALE
    max_scale = cfg.DATASET.MAX_SCALE
    max_translate = cfg.DATASET.MAX_TRANSLATE
    input_size = cfg.DATASET.INPUT_SIZE
    output_size = cfg.DATASET.OUTPUT_SIZE
    flip = cfg.DATASET.FLIP
    scale_type = cfg.DATASET.SCALE_TYPE

    flip_index = cfg.DATASET.FLIP_INDEX

    transforms = T.Compose(
        [
            T.RandomAffineTransform(
                input_size,
                output_size,
                max_rotation,
                min_scale,
                max_scale,
                scale_type,
                max_translate
            ),
            T.RandomHorizontalFlip(flip_index, output_size, flip),
            T.ToTensor(),
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ]
    )

    return transforms