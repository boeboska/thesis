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
from torchvision import transforms

from PIL import Image  # using pillow-simd for increased speed


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
        # print(path)

        weight_matrix = torch.load(path)

        if do_flip:
            # print("FLIP")
            weight_matrix = np.fliplr(weight_matrix.numpy())
            weight_matrix = torch.from_numpy(weight_matrix.copy())

        return weight_matrix


    def get_attention(self, folder, frame_index, side, do_flip):

        attention_masks = {}
        frame_index_start = ""
        frame_index_start = f"{0:010}"
        length = len(str(frame_index))
        frame_index_start = frame_index_start[:-length]
        frame_index = frame_index_start + str(frame_index)
        # print("frame _ index", frame_index)

        path = self.attention_path + "/" + folder + "/" + "image_0{}/data/".format(self.side_map[side])  + str(frame_index)

        count_mask = 0
        for subdir, dirs, files in os.walk(path):
             for file in files:

                 count_mask +=1
                 # print("file", file)
                 # probability = file.split("_")[1].split("jpg")[0][:-1]
                 # print("PROB", probability)
                 new_path = path + "/" + file
                 current_attention = self.attention_loader(new_path)

                 prob = float(file.split("_")[1].split(".jpg")[0])
                 # print(prob)
                 # print(type(prob))

                 # only load in the file if its prob is high enough
                 if prob >= self.attention_threshold:


                     if do_flip:
                         current_attention = current_attention.transpose(pil.FLIP_LEFT_RIGHT)


                     attention_map = transforms.ToTensor()(current_attention)

                     size_check = attention_map.clone()
                     size_check[size_check >= 0.8] = 1
                     size_check[size_check < 0.8] = 0
                     size = torch.sum(size_check).item()


                     attention_masks[file] = (size, attention_map)

                     # if len(attention_masks) > 30:
                     #     return attention_masks


                # if prob of current attention mask is not heigh enough, go to next one
                 else:
                     continue

        assert count_mask == 100, "There should be 100 attention masks saved for this kitti image. its now {}".format(count_mask)
        return attention_masks


    def get_attention_top_k(self, folder, frame_index, side, do_flip):
        "sort the dict with attention masks based on the attention mask probability"

        attention_masks = {}
        mask_amount = 0
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

                 # only save the attention mask probability
                 file = file.split("_")[1].split(".jpg")[0]


                 if do_flip:
                     current_attention = current_attention.transpose(pil.FLIP_LEFT_RIGHT)

                 # if there is already a attention mask with the same prob.. make a list with masks wich have the same prob
                 if file in attention_masks:
                     current_list = attention_masks[file]
                     current_list.append(current_attention)
                     attention_masks[file] = current_list
                     mask_amount += 1

                 # if there is not a attention mask with the same prob number
                 else:

                    attention_masks[file] = [current_attention]
                    mask_amount += 1

        return attention_masks, mask_amount



class KITTIRAWDataset(KITTIDataset):
    """KITTI dataset which loads the original velodyne depth maps for ground truth
    """
    def __init__(self, *args, **kwargs):
        super(KITTIRAWDataset, self).__init__(*args, **kwargs)

    def get_image_path(self, folder, frame_index, side):

        # print("frame index", frame_index)


        f_str = "{:010d}{}".format(frame_index, self.img_ext)

        # print("f str", f_str)

        image_path = os.path.join(
            self.data_path, folder, "image_0{}/data".format(self.side_map[side]), f_str)

        # print("IMG PATH", image_path)
        # print("IMG PATH", image_path)
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
