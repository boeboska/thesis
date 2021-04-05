# Copyright Niantic 2019. Patent Pending. All rights reserved.
#
# This software is licensed under the terms of the Monodepth2 licence
# which allows for non-commercial use only, the full terms of which are made
# available in the LICENSE file.

from __future__ import absolute_import, division, print_function

import torch
import os
import skimage.transform
import numpy as np
import PIL.Image as pil

from kitti_utils import generate_depth_map
from .mono_dataset import MonoDataset


class KITTIDataset(MonoDataset):
    """Superclass for different types of KITTI dataset loaders
    """
    def __init__(self, *args, **kwargs):
        super(KITTIDataset, self).__init__(*args, **kwargs)

        # NOTE: Make sure your intrinsics matrix is *normalized* by the original image size
        self.K = np.array([[0.58, 0, 0.5, 0],
                           [0, 1.92, 0.5, 0],
                           [0, 0, 1, 0],
                           [0, 0, 0, 1]], dtype=np.float32)

        self.full_res_shape = (1242, 375)
        self.side_map = {"2": 2, "3": 3, "l": 2, "r": 3}

    def check_depth(self):
        line = self.filenames[0].split()
        scene_name = line[0]
        frame_index = int(line[1])

        velo_filename = os.path.join(
            self.data_path,
            scene_name,
            "velodyne_points/data/{:010d}.bin".format(int(frame_index)))

        return os.path.isfile(velo_filename)

    def get_color(self, folder, frame_index, side, do_flip):
        color = self.loader(self.get_image_path(folder, frame_index, side))

        if do_flip:
            color = color.transpose(pil.FLIP_LEFT_RIGHT)

        return color

    def get_weight_matrix(self, folder, frame_index, side, do_flip):

        frame_index_start = f"{0:010}"
        length = len(str(frame_index))
        frame_index_start = frame_index_start[:-length]
        frame_index = frame_index_start + str(frame_index)
        # print("folder", folder)
        # print("frame index", frame_index)
        # print("side", side)
        path = self.weight_matrix_path + "/" + folder + "/" + "image_0{}/data/".format(self.side_map[side]) + str(
            frame_index + "/" + 'threshold_' + str(self.attention_threshold) + '_method_' + self.weight_mask_method + '.pt')
        print(path)

        weight_matrix = torch.load(path)

        if do_flip:
            weight_matrix = weight_matrix.transpose(pil.FLIP_LEFT_RIGHT)

        return weight_matrix



    def get_attention(self, folder, frame_index, side, do_flip):

        attention_masks = {}

        # print("folder", folder)
        # print("frame index", frame_index)

        # folder = '2011_09_26/2011_09_26_drive_0001_sync/'
        # frame_index = '0002/'
        # side = 'r'
        # print("folder", folder)
        frame_index_start = ""
        frame_index_start = f"{0:010}"
        length = len(str(frame_index))
        frame_index_start = frame_index_start[:-length]
        frame_index = frame_index_start + str(frame_index)
        # print("frame _ index", frame_index)

        path = self.attention_path + "/" + folder + "/" + "image_0{}/data/".format(self.side_map[side])  + str(frame_index)

        for subdir, dirs, files in os.walk(path):
             for file in files:
                 # print("file", file)
                 # probability = file.split("_")[1].split("jpg")[0][:-1]
                 # print("PROB", probability)
                 new_path = path + "/" + file
                 current_attention = self.attention_loader(new_path)

                 if do_flip:
                     current_attention = current_attention.transpose(pil.FLIP_LEFT_RIGHT)
                 attention_masks[file] = current_attention
                 # print(self.attention_loader(new_path))

        return attention_masks



class KITTIRAWDataset(KITTIDataset):
    """KITTI dataset which loads the original velodyne depth maps for ground truth
    """
    def __init__(self, *args, **kwargs):
        super(KITTIRAWDataset, self).__init__(*args, **kwargs)

    def get_image_path(self, folder, frame_index, side):
        f_str = "{:010d}{}".format(frame_index, self.img_ext)
        image_path = os.path.join(
            self.data_path, folder, "image_0{}/data".format(self.side_map[side]), f_str)
        return image_path

    def get_depth(self, folder, frame_index, side, do_flip):
        calib_path = os.path.join(self.data_path, folder.split("/")[0])

        velo_filename = os.path.join(
            self.data_path,
            folder,
            "velodyne_points/data/{:010d}.bin".format(int(frame_index)))

        depth_gt = generate_depth_map(calib_path, velo_filename, self.side_map[side])
        depth_gt = skimage.transform.resize(
            depth_gt, self.full_res_shape[::-1], order=0, preserve_range=True, mode='constant')

        if do_flip:
            depth_gt = np.fliplr(depth_gt)

        return depth_gt


class KITTIOdomDataset(KITTIDataset):
    """KITTI dataset for odometry training and testing
    """
    def __init__(self, *args, **kwargs):
        super(KITTIOdomDataset, self).__init__(*args, **kwargs)

    def get_image_path(self, folder, frame_index, side):
        f_str = "{:06d}{}".format(frame_index, self.img_ext)
        image_path = os.path.join(
            self.data_path,
            "sequences/{:02d}".format(int(folder)),
            "image_{}".format(self.side_map[side]),
            f_str)
        return image_path


class KITTIDepthDataset(KITTIDataset):
    """KITTI dataset which uses the updated ground truth depth maps
    """
    def __init__(self, *args, **kwargs):
        super(KITTIDepthDataset, self).__init__(*args, **kwargs)

    def get_image_path(self, folder, frame_index, side):
        f_str = "{:010d}{}".format(frame_index, self.img_ext)
        image_path = os.path.join(
            self.data_path,
            folder,
            "image_0{}/data".format(self.side_map[side]),
            f_str)
        return image_path

    def get_depth(self, folder, frame_index, side, do_flip):
        f_str = "{:010d}.png".format(frame_index)
        depth_path = os.path.join(
            self.data_path,
            folder,
            "proj_depth/groundtruth/image_0{}".format(self.side_map[side]),
            f_str)

        depth_gt = pil.open(depth_path)
        depth_gt = depth_gt.resize(self.full_res_shape, pil.NEAREST)
        depth_gt = np.array(depth_gt).astype(np.float32) / 256

        if do_flip:
            depth_gt = np.fliplr(depth_gt)

        return depth_gt
