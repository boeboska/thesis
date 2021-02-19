# Copyright Niantic 2019. Patent Pending. All rights reserved.
#
# This software is licensed under the terms of the Monodepth2 licence
# which allows for non-commercial use only, the full terms of which are made
# available in the LICENSE file.



from __future__ import absolute_import, division, print_function

import numpy as np
import time
import os

import torch
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import DataLoader
from tensorboardX import SummaryWriter

import json

from utils import *
from kitti_utils import *
from layers import *

import datasets
import networks
from IPython import embed

# attention

import matplotlib.pyplot as plt; plt.rcdefaults()
import matplotlib.pyplot as plt
import collections
from torchvision import transforms
import copy
import cv2
import matplotlib as mpl
import matplotlib.cm as cm

torch.backends.cudnn.enabled = False

torch.cuda.empty_cache()

import os

# os.environ["KMP_DUPLICATE_LIB_OK"]= True
#
# os.environ["CUDA_VISIBLE_DEVICES"] = '0'
# os.environ["KMP_DUPLICATE_LIB_OK"]= "True"
# os.environ['KMP_DUPLICATE_LIB_OK']='True'
# os.environ["CUDA_VISIBLE_DEVICES"] ='True'


class Trainer:
    torch.cuda.empty_cache()


    def __init__(self, options):
        self.opt = options
        self.log_path = os.path.join(self.opt.log_dir, self.opt.model_name)

        # checking height and width are multiples of 32
        assert self.opt.height % 32 == 0, "'height' must be a multiple of 32"
        assert self.opt.width % 32 == 0, "'width' must be a multiple of 32"

        self.models = {}
        self.parameters_to_train = []

        self.device = torch.device("cpu" if self.opt.no_cuda else "cuda")

        print("device", self.device)

        self.num_scales = len(self.opt.scales)
        self.num_input_frames = len(self.opt.frame_ids)
        self.num_pose_frames = 2 if self.opt.pose_model_input == "pairs" else self.num_input_frames

        assert self.opt.frame_ids[0] == 0, "frame_ids must start with 0"

        self.use_pose_net = not (self.opt.use_stereo and self.opt.frame_ids == [0])

        if self.opt.use_stereo:
            self.opt.frame_ids.append("s")

        self.models["encoder"] = networks.ResnetEncoder(
            self.opt.num_layers, self.opt.weights_init == "pretrained")
        self.models["encoder"].to(self.device)
        self.parameters_to_train += list(self.models["encoder"].parameters())

        self.models["depth"] = networks.DepthDecoder(
            self.models["encoder"].num_ch_enc, self.opt.scales)
        self.models["depth"].to(self.device)
        self.parameters_to_train += list(self.models["depth"].parameters())

        if self.use_pose_net:
            if self.opt.pose_model_type == "separate_resnet":
                self.models["pose_encoder"] = networks.ResnetEncoder(
                    self.opt.num_layers,
                    self.opt.weights_init == "pretrained",
                    num_input_images=self.num_pose_frames)

                self.models["pose_encoder"].to(self.device)
                self.parameters_to_train += list(self.models["pose_encoder"].parameters())

                self.models["pose"] = networks.PoseDecoder(
                    self.models["pose_encoder"].num_ch_enc,
                    num_input_features=1,
                    num_frames_to_predict_for=2)

            elif self.opt.pose_model_type == "shared":
                self.models["pose"] = networks.PoseDecoder(
                    self.models["encoder"].num_ch_enc, self.num_pose_frames)

            elif self.opt.pose_model_type == "posecnn":
                self.models["pose"] = networks.PoseCNN(
                    self.num_input_frames if self.opt.pose_model_input == "all" else 2)

            self.models["pose"].to(self.device)
            self.parameters_to_train += list(self.models["pose"].parameters())

        if self.opt.predictive_mask:
            assert self.opt.disable_automasking, \
                "When using predictive_mask, please disable automasking with --disable_automasking"

            # Our implementation of the predictive masking baseline has the the same architecture
            # as our depth decoder. We predict a separate mask for each source frame.
            self.models["predictive_mask"] = networks.DepthDecoder(
                self.models["encoder"].num_ch_enc, self.opt.scales,
                num_output_channels=(len(self.opt.frame_ids) - 1))
            self.models["predictive_mask"].to(self.device)
            self.parameters_to_train += list(self.models["predictive_mask"].parameters())

        self.model_optimizer = optim.Adam(self.parameters_to_train, self.opt.learning_rate)
        self.model_lr_scheduler = optim.lr_scheduler.StepLR(
            self.model_optimizer, self.opt.scheduler_step_size, 0.1)

        if self.opt.load_weights_folder is not None:
            self.load_model()

        print("Training model named:\n  ", self.opt.model_name)
        print("Models and tensorboard events files are saved to:\n  ", self.opt.log_dir)
        print("Training is using:\n  ", self.device)

        # data
        datasets_dict = {"kitti": datasets.KITTIRAWDataset,
                         "kitti_odom": datasets.KITTIOdomDataset}
        self.dataset = datasets_dict[self.opt.dataset]

        fpath = os.path.join(os.path.dirname(__file__), "splits", self.opt.split, "{}_files.txt")

        train_filenames = readlines(fpath.format("train"))
        val_filenames = readlines(fpath.format("val"))
        img_ext = '.png' if self.opt.png else '.jpg'

        num_train_samples = len(train_filenames)
        self.num_total_steps = num_train_samples // self.opt.batch_size * self.opt.num_epochs


        train_dataset = self.dataset(
            self.opt.attention_mask_loss,  self.opt.edge_loss, self.opt.data_path, self.opt.attention_path, self.opt.attention_threshold, train_filenames, self.opt.height, self.opt.width,
            self.opt.frame_ids, 4, is_train=True, img_ext=img_ext)

        self.train_loader = DataLoader(
            train_dataset, self.opt.batch_size, True,
            num_workers=self.opt.num_workers, pin_memory=True, drop_last=True)

        val_dataset = self.dataset(
            self.opt.attention_mask_loss, self.opt.edge_loss, self.opt.data_path, self.opt.attention_path, self.opt.attention_threshold, val_filenames, self.opt.height, self.opt.width,
            self.opt.frame_ids, 4, is_train=False, img_ext=img_ext)
        self.val_loader = DataLoader(
            val_dataset, self.opt.batch_size, True,
            num_workers=self.opt.num_workers, pin_memory=True, drop_last=True)
        self.val_iter = iter(self.val_loader)

        self.writers = {}
        for mode in ["train", "val"]:
            self.writers[mode] = SummaryWriter(os.path.join(self.log_path, mode))

        if not self.opt.no_ssim:
            self.ssim = SSIM()
            self.ssim.to(self.device)

        self.backproject_depth = {}
        self.project_3d = {}
        for scale in self.opt.scales:
            h = self.opt.height // (2 ** scale)
            w = self.opt.width // (2 ** scale)

            self.backproject_depth[scale] = BackprojectDepth(self.opt.batch_size, h, w)
            self.backproject_depth[scale].to(self.device)

            self.project_3d[scale] = Project3D(self.opt.batch_size, h, w)
            self.project_3d[scale].to(self.device)

        self.depth_metric_names = [
            "de/abs_rel", "de/sq_rel", "de/rms", "de/log_rms", "da/a1", "da/a2", "da/a3"]

        print("Using split:\n  ", self.opt.split)
        print("There are {:d} training items and {:d} validation items\n".format(
            len(train_dataset), len(val_dataset)))

        self.save_opts()

    def set_train(self):
        """Convert all models to training mode
        """
        for m in self.models.values():
            m.train()

    def set_eval(self):
        """Convert all models to testing/evaluation mode
        """
        for m in self.models.values():
            m.eval()

    def train(self):
        """Run the entire training pipeline
        """
        self.epoch = 0
        self.step = 0
        self.start_time = time.time()
        for self.epoch in range(self.opt.num_epochs):
            self.run_epoch()
            if (self.epoch + 1) % self.opt.save_frequency == 0:
                self.save_model()

    def run_epoch(self):
        """Run a single epoch of training and validation
        """
        self.model_lr_scheduler.step()

        print("Training")
        self.set_train()

        prob_sum_mask = {}

        for batch_idx, inputs in enumerate(self.train_loader):
            print(batch_idx)



            before_op_time = time.time()

            outputs, losses = self.process_batch(inputs, batch_idx)
            # breakpoint()
            self.model_optimizer.zero_grad()

            losses["loss"].backward()
            self.model_optimizer.step()

            duration = time.time() - before_op_time

            # log less frequently after the first 2000 steps to save time & disk space
            early_phase = batch_idx % self.opt.log_frequency == 0 and self.step < 2000
            late_phase = self.step % 2000 == 0

            if early_phase or late_phase:
                self.log_time(batch_idx, duration, losses["loss"].cpu().data)

                if "depth_gt" in inputs:
                    self.compute_depth_losses(inputs, outputs, losses)

                self.log("train", inputs, outputs, losses)
                self.val(batch_idx)

            self.step += 1

    def process_batch(self, inputs, batch_idx):
        """Pass a minibatch through the network and generate images and losses
        """
        for key, ipt in inputs.items():
            inputs[key] = ipt.to(self.device)

        if self.opt.pose_model_type == "shared":
            # If we are using a shared encoder for both depth and pose (as advocated
            # in monodepthv1), then all images are fed separately through the depth encoder.
            all_color_aug = torch.cat([inputs[("color_aug", i, 0)] for i in self.opt.frame_ids])
            all_features = self.models["encoder"](all_color_aug)
            all_features = [torch.split(f, self.opt.batch_size) for f in all_features]

            features = {}
            for i, k in enumerate(self.opt.frame_ids):
                features[k] = [f[i] for f in all_features]

            outputs = self.models["depth"](features[0])
        else:
            # Otherwise, we only feed the image with frame_id 0 through the depth encoder
            features = self.models["encoder"](inputs["color_aug", 0, 0])
            outputs = self.models["depth"](features)


        if self.opt.predictive_mask:
            outputs["predictive_mask"] = self.models["predictive_mask"](features)

        if self.use_pose_net:
            outputs.update(self.predict_poses(inputs, features))

        self.generate_images_pred(inputs, outputs)


        losses = self.compute_losses(inputs, outputs, batch_idx)

        return outputs, losses

    def predict_poses(self, inputs, features):
        """Predict poses between input frames for monocular sequences.
        """
        outputs = {}
        if self.num_pose_frames == 2:
            # In this setting, we compute the pose to each source frame via a
            # separate forward pass through the pose network.

            # select what features the pose network takes as input
            if self.opt.pose_model_type == "shared":
                pose_feats = {f_i: features[f_i] for f_i in self.opt.frame_ids}
            else:
                pose_feats = {f_i: inputs["color_aug", f_i, 0] for f_i in self.opt.frame_ids}

            for f_i in self.opt.frame_ids[1:]:
                if f_i != "s":
                    # To maintain ordering we always pass frames in temporal order
                    if f_i < 0:
                        pose_inputs = [pose_feats[f_i], pose_feats[0]]
                    else:
                        pose_inputs = [pose_feats[0], pose_feats[f_i]]

                    if self.opt.pose_model_type == "separate_resnet":
                        pose_inputs = [self.models["pose_encoder"](torch.cat(pose_inputs, 1))]
                    elif self.opt.pose_model_type == "posecnn":
                        pose_inputs = torch.cat(pose_inputs, 1)

                    axisangle, translation = self.models["pose"](pose_inputs)
                    outputs[("axisangle", 0, f_i)] = axisangle
                    outputs[("translation", 0, f_i)] = translation

                    # Invert the matrix if the frame id is negative
                    outputs[("cam_T_cam", 0, f_i)] = transformation_from_parameters(
                        axisangle[:, 0], translation[:, 0], invert=(f_i < 0))

        else:
            # Here we input all frames to the pose net (and predict all poses) together
            if self.opt.pose_model_type in ["separate_resnet", "posecnn"]:
                pose_inputs = torch.cat(
                    [inputs[("color_aug", i, 0)] for i in self.opt.frame_ids if i != "s"], 1)

                if self.opt.pose_model_type == "separate_resnet":
                    pose_inputs = [self.models["pose_encoder"](pose_inputs)]

            elif self.opt.pose_model_type == "shared":
                pose_inputs = [features[i] for i in self.opt.frame_ids if i != "s"]

            axisangle, translation = self.models["pose"](pose_inputs)

            for i, f_i in enumerate(self.opt.frame_ids[1:]):
                if f_i != "s":
                    outputs[("axisangle", 0, f_i)] = axisangle
                    outputs[("translation", 0, f_i)] = translation
                    outputs[("cam_T_cam", 0, f_i)] = transformation_from_parameters(
                        axisangle[:, i], translation[:, i])

        return outputs

    def val(self, batch_idx):
        """Validate the model on a single minibatch
        """
        self.set_eval()
        try:
            inputs = self.val_iter.next()
        except StopIteration:
            self.val_iter = iter(self.val_loader)
            inputs = self.val_iter.next()

        with torch.no_grad():
            outputs, losses = self.process_batch(inputs, batch_idx)

            if "depth_gt" in inputs:
                self.compute_depth_losses(inputs, outputs, losses)

            self.log("val", inputs, outputs, losses)
            del inputs, outputs, losses

        self.set_train()

    def edge_dection_loss(self, scale, outputs, inputs, batch_idx):

        low_edge_value = 0.15
        high_edge_value = 0.16

        edge_loss = []

        disp = outputs[("disp", 0)]



        # disp_resized = torch.nn.functional.interpolate(
        #     disp, (self.opt.height, self.opt.width), mode="bilinear", align_corners=False)
        #
        # # Saving colormapped depth image
        # disp_resized_np = disp_resized.squeeze().cpu().detach().numpy()
        # vmax = np.percentile(disp_resized_np, 95)
        # normalizer = mpl.colors.Normalize(vmin=disp_resized_np.min(), vmax=vmax)
        # mapper = cm.ScalarMappable(norm=normalizer, cmap='magma')
        # # breakpoint()
        # colormapped_im = (mapper.to_rgba(disp_resized_np) * 255).astype(np.uint8)



        # disp = disp.squeeze(0).squeeze(0)


        # select the depth image
        # depth = outputs[('depth', 0, scale)]  # 1, 1, 192, 640

        original_img = inputs["color_aug", 0, 0]
        original_img = np.array(original_img.cpu().detach().numpy())
        original_img = np.swapaxes(original_img, 1, 2)
        original_img = np.swapaxes(original_img, 2, 3)


        # print("type original img", type(original_img))
        # print("size", original_img.shape)

        # select the mask images corresponding to the target image
        attention_mask = inputs['attention'].to(self.device)

        # breakpoint()
        original_attention = torch.clone(attention_mask)

        # everywhing which doesn't belong to the mask, set off
        attention_mask[attention_mask < 0.7] = 0

        # everywhing which doesn't belong to the mask, set on
        attention_mask[attention_mask >= 0.7] = 1

        # create only the depth pixels that lie inside the attention mask
        depth_mask = attention_mask * disp


        depth_mask_test = attention_mask * disp


        depth_mask_test = np.array(depth_mask_test.cpu().detach().numpy().astype(np.float32))

        # filter out dimension for correct plotting
        disp = disp.squeeze(1)
        disp = np.array(disp.cpu().detach().numpy())

        depth_mask = np.array(depth_mask.cpu().detach().numpy())


        # * 255 for canny edge working
        depth_mask = depth_mask * 255

        # cast to int for correct canny input
        depth_mask = np.uint8(depth_mask)

        for batch in range(self.opt.batch_size):
            # breakpoint()
            for attention in range(depth_mask.shape[1]):

                # dont calculate edged when attention mask is to big. Because then there will be noise to training. Edge detection won't work as we want
                if attention_mask[batch][attention].sum() > self.opt.attention_sum:
                    continue

                edges_disp = cv2.Canny(depth_mask[batch][attention], low_edge_value*255, high_edge_value*255, apertureSize=3, L2gradient=False)
                kernel = np.ones((5, 5), np.uint8)
                erosion = cv2.erode(depth_mask[batch][attention], kernel, iterations=3)
                result = cv2.bitwise_and(edges_disp, edges_disp, mask=erosion)

                if batch_idx % 50 == 0:
                    fig, ax = plt.subplots(7, 1)

                    ax[0].imshow(original_img[batch])
                    ax[0].title.set_text('Original image')

                    ax[1].imshow(disp[batch])
                    ax[1].title.set_text('disp')

                    ax[2].imshow(original_attention.cpu()[batch][attention])
                    ax[2].title.set_text('Attention mask')

                    ax[3].imshow(depth_mask[batch][attention])
                    ax[3].title.set_text('depth mask')

                    ax[4].imshow(depth_mask_test[batch][attention])
                    ax[4].title.set_text('depth mask TEST')

                    ax[5].imshow(edges_disp)
                    ax[5].title.set_text('Edges disp before erosion')

                    ax[6].imshow(result)
                    ax[6].title.set_text('Edges disp after erosion')
                    # plt.show()
                    # plt.save()
                    fig.savefig('edge_experiment/epoch_{}batch_{}sum_{}.png'.format(self.epoch, batch_idx, result.sum()))
                    plt.close()

                edge_loss.append(result.sum())

        edge_loss = np.array(edge_loss)
        return np.mean(edge_loss)


    def attention_depth_loss(self, scale, outputs, inputs):

        start = time.time()

        # attention_loss = []
        # loop over the rescaled depth images. all depth image are size 1, 1, 192, 640
        depth = outputs[('depth', 0, scale)] # 1, 1, 192, 640

        # inputs[('attention', '18_0.99.jpg')].size()   = 1, 1, 192, 640
        # breakpoint()
        # fig, ax = plt.subplots(2, 1)

        # target_img = inputs["color_aug", 0, 0].squeeze(0).permute(1, 2, 0).cpu()
        # ax[0].imshow(target_img)
        # attention_prob = key[1].split('_')[1].split('.jpg')[0]

        # ax[1].imshow(inputs[key].squeeze(0).squeeze(0).cpu(), cmap= 'cividis')

        # plt.show()

        attention_mask = inputs['attention'].to(self.device)

        breakpoint()




        # everywhing which doesn't belong to the mask, set off
        attention_mask[attention_mask < 0.8] = 0

        # everything which belongs to the mask, set onn
        attention_mask[attention_mask >= 0.8] = 1


        # here you only keep the depth pixels where the mask is
        depth_mask = attention_mask * depth
        depth_mask = depth_mask.to(self.device)

        # calculate mean depth
        depth_mask = depth_mask.view(depth_mask.size()[0], -1).to(self.device)
        row_sum = depth_mask.sum(dim=1).to(self.device)
        mask = depth_mask != 0
        mask = mask.to(self.device)
        non_zero = mask.sum(dim=1).to(self.device)
        mean_depth = row_sum / non_zero

        # check if depth pixel is inside the mask value
        # this is the condition for the thorch.where function. Because you only want to calculate the loss for depth values which
        # are withing the depth mask (depth image DOT attention mask). So you only want the non zero values
        cond = depth_mask != 0
        cond = cond.to(self.device)


        depth_mask = depth_mask * 1000000
        depth_mask = depth_mask.type(torch.int64).to(self.device)
        mean_depth = mean_depth * 1000000
        mean_depth = mean_depth.to(dtype=torch.int).to(self.device)

        # squeeze the mean depth value over the dimens for a correct torch where
        mean_depth = mean_depth.unsqueeze(-1).to(self.device)
        mean_depth = mean_depth.repeat_interleave(depth_mask.size()[1], dim=1).to(self.device)

        # # if condition is true, the loss is the depth pixel value withing the depth mask - avg depth value. otherwise loss = 0
        loss = torch.where(cond.to(self.device), abs(depth_mask.to(self.device) - mean_depth.to(self.device)), torch.tensor(0).to(self.device)).to(self.device)


        loss_b = loss / 1000000.

        row_sum = loss_b.sum(dim=1)

        mask = loss_b != 0
        non_zero = mask.sum(dim=1)

        attention_loss = row_sum / non_zero



        # for key in inputs.keys():
        #     if key[0] == 'attention':
        #
        #         # breakpoint()
        #
        #
        #         attention_mask = inputs[key]
        #
        #
        #
        #
        #         # try to skip out ways and sky because they are nosie for training
        #
        #         if attention_mask.sum() > self.opt.attention_sum:
        #             # print("found", attention_mask.sum())
        #             continue
        #
        #         # 122. 880 pixels in attention mask
        #
        #
        #         # here you only keep the depth pixels where the mask is
        #         depth_mask = attention_mask * depth
        #
        #         # calculate depth mean by summing all depth values without zero values because zero values are not inside the mask
        #         row_sum = depth_mask.sum(dim=1) # sum of each row value in tensor
        #         non_zero = (depth_mask != 0).sum(dim=1) # count how many non zero values there are in each tensor row
        #         mean_depth = row_sum.sum() / non_zero.sum() # divide row total by the amount of non zero values
        #
        #         # check if depth pixel is inside the mask value
        #         # this is the condition for the thorch.where function. Because you only want to calculate the loss for depth values which
        #         # are withing the depth mask (depth image DOT attention mask). So you only want the non zero values
        #         cond = depth_mask != 0
        #
        #         # if condition is true, the loss is the depth pixel value withing the depth mask - avg depth value. otherwise loss = 0
        #         # loss = torch.where(cond, torch.FloatTensor.abs(depth_mask - mean_depth), 0)
        #         depth_mask = depth_mask * 1000000
        #         depth_mask = depth_mask.type(torch.int64)
        #         mean_depth = int(mean_depth * 1000000)
        #
        #         loss = torch.where(cond, abs(depth_mask - mean_depth), 0)
        #         loss_b = loss / 1000000.  # 1, 1, 192, 640
        #
        #         # example to show that multiplying and dividing by 1000000 doesn't change loss
        #         # depth mask
        #         # tensor([[[[0.0316, 0.8090],
        #         #           [0.0000, 0.6262]]]])
        #
        #         # mean depth
        #         # tensor(0.4889)
        #
        #         # loss
        #         # tensor([[[[0.4574, 0.3201],
        #         #           [0.0000, 0.1373]]]])
        #
        #         row_sum = loss_b.sum(dim=1)
        #         non_zero = (loss_b != 0).sum(dim=1)
        #         mean_loss = row_sum.sum() / non_zero.sum()  # mean loss ~ 0.0010
        #
        #         # mean loss for this attention mask
        #         attention_loss.append(mean_loss)
        # try:
        #     attention_loss = sum(attention_loss) / len(attention_loss)
        # except:
        #     print("attention 0")
        #     attention_loss = 0

        return attention_loss.mean()

    def attention_mask_loss(self, inputs):

        # select the mask images corresponding to the target image
        attention_mask = inputs['attention'].to(self.device)

        # everywhing which doesn't belong to the mask, set off
        attention_mask[attention_mask < 0.7] = 0

        # everything which belongs to the mask, set onn
        attention_mask[attention_mask >= 0.7] = 1

        # batch x amount of attention. sum every attention tensor within the batch size
        attention_sum = attention_mask.sum(-1).sum(-1)

        # within a batch you have multiple attention masks. Decide how much weight each attention mask will receive.
        # The smaller the mask the more weight it'll receive.

        # summ all the attention maps within 1 batch size
        # this is the summ of all the attention maps withing 1 batch
        batch_sum = attention_sum.sum(-1).unsqueeze(1)

        # determine weight
        # https://math.stackexchange.com/questions/3817854/formula-for-inverse-weighted-average-smaller-value-gets-higher-weight
        v = attention_sum / batch_sum
        v = 1 / v

        # remove inf number because 1 / 0 = inf
        v[v == float('inf')] = 0

        t = v.sum(-1).unsqueeze(1)

        attention_weight_matrix = v / t

        # remove nan
        attention_weight_matrix[attention_weight_matrix != attention_weight_matrix] = 0

        assert round(attention_weight_matrix.sum(-1).sum(-1).item()) == self.opt.batch_size, "The sum weight doesn't sum up to 1 per batch, namely:{}".format(attention_weight_matrix.sum(-1).sum(-1))
        # create ones matrix


        ones = torch.ones(size=(self.opt.batch_size, attention_mask.size()[1], attention_mask.size()[2], attention_mask.size()[3])).to(self.device)

        # multiply original attention masks by their weight
        # breakpoint()
        attention_weight_matrix = attention_weight_matrix.unsqueeze(-1).unsqueeze(-1)
        weight_attention_mask = attention_mask * attention_weight_matrix


        end_weight = ones + weight_attention_mask

        return end_weight



    def generate_images_pred(self, inputs, outputs):
        """Generate the warped (reprojected) color images for a minibatch.
        Generated images are saved into the `outputs` dictionary.
        """
        for scale in self.opt.scales:
            disp = outputs[("disp", scale)]
            if self.opt.v1_multiscale:
                source_scale = scale
            else:
                disp = F.interpolate(
                    disp, [self.opt.height, self.opt.width], mode="bilinear", align_corners=False)
                source_scale = 0

            _, depth = disp_to_depth(disp, self.opt.min_depth, self.opt.max_depth)

            outputs[("depth", 0, scale)] = depth

            for i, frame_id in enumerate(self.opt.frame_ids[1:]):

                if frame_id == "s":
                    T = inputs["stereo_T"]
                else:
                    T = outputs[("cam_T_cam", 0, frame_id)]

                # from the authors of https://arxiv.org/abs/1712.00175
                if self.opt.pose_model_type == "posecnn":

                    axisangle = outputs[("axisangle", 0, frame_id)]
                    translation = outputs[("translation", 0, frame_id)]

                    inv_depth = 1 / depth
                    mean_inv_depth = inv_depth.mean(3, True).mean(2, True)

                    T = transformation_from_parameters(
                        axisangle[:, 0], translation[:, 0] * mean_inv_depth[:, 0], frame_id < 0)

                cam_points = self.backproject_depth[source_scale](
                    depth, inputs[("inv_K", source_scale)])
                pix_coords = self.project_3d[source_scale](
                    cam_points, inputs[("K", source_scale)], T)

                outputs[("sample", frame_id, scale)] = pix_coords

                outputs[("color", frame_id, scale)] = F.grid_sample(
                    inputs[("color", frame_id, source_scale)],
                    outputs[("sample", frame_id, scale)],
                    padding_mode="border")

                if not self.opt.disable_automasking:
                    outputs[("color_identity", frame_id, scale)] = \
                        inputs[("color", frame_id, source_scale)]

    def compute_reprojection_loss(self, pred, target, inputs):
        """Computes reprojection loss between a batch of predicted and target images
        """
        abs_diff = torch.abs(target - pred)
        l1_loss = abs_diff.mean(1, True)

        if self.opt.attention_mask_loss == True:
            attention_weight = self.attention_mask_loss(inputs)

        if self.opt.no_ssim:
            reprojection_loss = l1_loss
        else:
            if self.opt.attention_mask_loss:


                ssim_loss = self.ssim(pred, target).mean(1, True)
                reprojection_loss = 0.85 * ssim_loss *  attention_weight + 0.15 * l1_loss * attention_weight
                # breakpoint()
            else:
                ssim_loss = self.ssim(pred, target).mean(1, True)

                reprojection_loss = 0.85 * ssim_loss + 0.15 * l1_loss



        return reprojection_loss

    def compute_losses(self, inputs, outputs, batch_idx):
        """Compute the reprojection and smoothness losses for a minibatch
        """
        losses = {}
        total_loss = 0

        # self.opt.scales = # help = "scales used in the loss", # default = [0, 1, 2, 3])
        for scale in self.opt.scales:
            loss = 0
            reprojection_losses = []

            if self.opt.v1_multiscale:
                source_scale = scale
            else:
                source_scale = 0

            disp = outputs[("disp", scale)]
            color = inputs[("color", 0, scale)]
            target = inputs[("color", 0, source_scale)]
            # disp, color, target: torch.Size([1, 1, 192, 640]), torch.Size([1, 3, 192, 640]), torch.Size([1, 3, 192, 640])

            for frame_id in self.opt.frame_ids[1:]:

                # this function selects frame ID -1 and +1. 0 is the target image. So it created two prediction images

                pred = outputs[("color", frame_id, scale)]
                reprojection_losses.append(self.compute_reprojection_loss(pred, target, inputs))

            reprojection_losses = torch.cat(reprojection_losses, 1)

            if self.opt.edge_loss:

                edge_loss = self.edge_dection_loss(scale, outputs, inputs, batch_idx)

                # attention_loss = self.attention_depth_loss(scale, outputs, inputs)

                # print("ATTENTION", attention_loss)

                # loss += self.opt.attention_weight * attention_loss / (2 ** scale)

                # scale 0 = torch.Size([1, 3, 192, 640])
                # scale 1 = torch.Size([1, 3, 96, 320])
                # scale 2 = torch.Size([1, 3, 48, 160])
                # scale 3 = torch.Size([1, 3, 24, 80])

                # print(0, 1000 * (self.opt.disparity_smoothness * smooth_loss / (2 ** 0)))
                # print(1, 1000 * (self.opt.disparity_smoothness * smooth_loss / (2 ** 1)))
                # print(2, 1000 * (self.opt.disparity_smoothness * smooth_loss / (2 ** 2)))
                # print(3, 1000 * (self.opt.disparity_smoothness * smooth_loss / (2 ** 3)))

                # 0 tensor(0.0057, device='cuda:0', grad_fn= < MulBackward0 >)
                # 1 tensor(0.0029, device='cuda:0', grad_fn= < MulBackward0 >)
                # 2 tensor(0.0014, device='cuda:0', grad_fn= < MulBackward0 >)
                # 3 tensor(0.0007, device='cuda:0', grad_fn= < MulBackward0 >)

            # FALSE
            if not self.opt.disable_automasking:
                identity_reprojection_losses = []
                for frame_id in self.opt.frame_ids[1:]:
                    pred = inputs[("color", frame_id, source_scale)]
                    identity_reprojection_losses.append(
                        self.compute_reprojection_loss(pred, target, inputs))

                identity_reprojection_losses = torch.cat(identity_reprojection_losses, 1)

                if self.opt.avg_reprojection:
                    identity_reprojection_loss = identity_reprojection_losses.mean(1, keepdim=True)
                else:
                    # save both images, and do min all at once below
                    identity_reprojection_loss = identity_reprojection_losses


            # FALSE
            elif self.opt.predictive_mask:
                # use the predicted mask
                mask = outputs["predictive_mask"]["disp", scale]
                if not self.opt.v1_multiscale:
                    mask = F.interpolate(
                        mask, [self.opt.height, self.opt.width],
                        mode="bilinear", align_corners=False)

                reprojection_losses *= mask

                # add a loss pushing mask to 1 (using nn.BCELoss for stability)
                weighting_loss = 0.2 * nn.BCELoss()(mask, torch.ones(mask.shape).cuda())
                loss += weighting_loss.mean()

            # FALSE
            if self.opt.avg_reprojection:
                reprojection_loss = reprojection_losses.mean(1, keepdim=True)
            else:
                reprojection_loss = reprojection_losses

            # This one is performed
            if not self.opt.disable_automasking:
                # add random numbers to break ties
                identity_reprojection_loss += torch.randn(
                    identity_reprojection_loss.shape).cuda() * 0.00001

                combined = torch.cat((identity_reprojection_loss, reprojection_loss), dim=1)
            else:
                combined = reprojection_loss

            # FALSE
            if combined.shape[1] == 1:
                to_optimise = combined
            else:
                to_optimise, idxs = torch.min(combined, dim=1)

            # This one is performed
            if not self.opt.disable_automasking:
                outputs["identity_selection/{}".format(scale)] = (
                    idxs > identity_reprojection_loss.shape[1] - 1).float()

            # this is still the reprojection loss
            # print("reporjection", to_optimise.mean())
            loss += to_optimise.mean()

            # print("loss na reprojection", loss)

            # calculate smooth loss
            mean_disp = disp.mean(2, True).mean(3, True)
            norm_disp = disp / (mean_disp + 1e-7)
            smooth_loss = get_smooth_loss(norm_disp, color)

            # print("smooth loss zonder calc", smooth_loss)
            # print("smooth", self.opt.disparity_smoothness * smooth_loss / (2 ** scale))
            # print("scale", scale)

            # print(0, 1000 * (self.opt.disparity_smoothness * smooth_loss / (2 ** 0)))
            # print(1, 1000 * (self.opt.disparity_smoothness * smooth_loss / (2 ** 1)))
            # print(2, 1000 * (self.opt.disparity_smoothness * smooth_loss / (2 ** 2)))
            # print(3, 1000 * (self.opt.disparity_smoothness * smooth_loss / (2 ** 3)))
            # 0 tensor(0.0057, device='cuda:0', grad_fn= < MulBackward0 >)
            # 1 tensor(0.0029, device='cuda:0', grad_fn= < MulBackward0 >)
            # 2 tensor(0.0014, device='cuda:0', grad_fn= < MulBackward0 >)
            # 3 tensor(0.0007, device='cuda:0', grad_fn= < MulBackward0 >)


            # add smooth loss to reprojection loss
            loss += self.opt.disparity_smoothness * smooth_loss / (2 ** scale)
            # print("loss na smooth", loss)

            total_loss += loss
            losses["loss/{}".format(scale)] = loss


        total_loss /= self.num_scales
        losses["loss"] = total_loss
        return losses

    def compute_depth_losses(self, inputs, outputs, losses):
        """Compute depth metrics, to allow monitoring during training

        This isn't particularly accurate as it averages over the entire batch,
        so is only used to give an indication of validation performance
        """
        depth_pred = outputs[("depth", 0, 0)]
        depth_pred = torch.clamp(F.interpolate(
            depth_pred, [375, 1242], mode="bilinear", align_corners=False), 1e-3, 80)
        depth_pred = depth_pred.detach()

        depth_gt = inputs["depth_gt"]
        mask = depth_gt > 0

        # garg/eigen crop
        crop_mask = torch.zeros_like(mask)
        crop_mask[:, :, 153:371, 44:1197] = 1
        mask = mask * crop_mask

        depth_gt = depth_gt[mask]
        depth_pred = depth_pred[mask]
        depth_pred *= torch.median(depth_gt) / torch.median(depth_pred)

        depth_pred = torch.clamp(depth_pred, min=1e-3, max=80)

        depth_errors = compute_depth_errors(depth_gt, depth_pred)

        for i, metric in enumerate(self.depth_metric_names):
            losses[metric] = np.array(depth_errors[i].cpu())

    def log_time(self, batch_idx, duration, loss):
        """Print a logging statement to the terminal
        """
        samples_per_sec = self.opt.batch_size / duration
        time_sofar = time.time() - self.start_time
        training_time_left = (
            self.num_total_steps / self.step - 1.0) * time_sofar if self.step > 0 else 0
        print_string = "epoch {:>3} | batch {:>6} | examples/s: {:5.1f}" + \
            " | loss: {:.5f} | time elapsed: {} | time left: {}"
        print(print_string.format(self.epoch, batch_idx, samples_per_sec, loss,
                                  sec_to_hm_str(time_sofar), sec_to_hm_str(training_time_left)))

    def log(self, mode, inputs, outputs, losses):
        """Write an event to the tensorboard events file
        """
        writer = self.writers[mode]
        for l, v in losses.items():
            writer.add_scalar("{}".format(l), v, self.step)

        for j in range(min(4, self.opt.batch_size)):  # write a maxmimum of four images
            for s in self.opt.scales:
                for frame_id in self.opt.frame_ids:
                    writer.add_image(
                        "color_{}_{}/{}".format(frame_id, s, j),
                        inputs[("color", frame_id, s)][j].data, self.step)
                    if s == 0 and frame_id != 0:
                        writer.add_image(
                            "color_pred_{}_{}/{}".format(frame_id, s, j),
                            outputs[("color", frame_id, s)][j].data, self.step)

                writer.add_image(
                    "disp_{}/{}".format(s, j),
                    normalize_image(outputs[("disp", s)][j]), self.step)

                if self.opt.predictive_mask:
                    for f_idx, frame_id in enumerate(self.opt.frame_ids[1:]):
                        writer.add_image(
                            "predictive_mask_{}_{}/{}".format(frame_id, s, j),
                            outputs["predictive_mask"][("disp", s)][j, f_idx][None, ...],
                            self.step)

                elif not self.opt.disable_automasking:
                    writer.add_image(
                        "automask_{}/{}".format(s, j),
                        outputs["identity_selection/{}".format(s)][j][None, ...], self.step)

    def save_opts(self):
        """Save options to disk so we know what we ran this experiment with
        """
        models_dir = os.path.join(self.log_path, "models")
        if not os.path.exists(models_dir):
            os.makedirs(models_dir)
        to_save = self.opt.__dict__.copy()

        with open(os.path.join(models_dir, 'opt.json'), 'w') as f:
            json.dump(to_save, f, indent=2)

    def save_model(self):
        """Save model weights to disk
        """
        save_folder = os.path.join(self.log_path, "models", "weights_{}".format(self.epoch))
        if not os.path.exists(save_folder):
            os.makedirs(save_folder)

        for model_name, model in self.models.items():
            save_path = os.path.join(save_folder, "{}.pth".format(model_name))
            to_save = model.state_dict()
            if model_name == 'encoder':
                # save the sizes - these are needed at prediction time
                to_save['height'] = self.opt.height
                to_save['width'] = self.opt.width
                to_save['use_stereo'] = self.opt.use_stereo
            torch.save(to_save, save_path)

        save_path = os.path.join(save_folder, "{}.pth".format("adam"))
        torch.save(self.model_optimizer.state_dict(), save_path)

    def load_model(self):
        """Load model(s) from disk
        """
        self.opt.load_weights_folder = os.path.expanduser(self.opt.load_weights_folder)

        assert os.path.isdir(self.opt.load_weights_folder), \
            "Cannot find folder {}".format(self.opt.load_weights_folder)
        print("loading model from folder {}".format(self.opt.load_weights_folder))

        for n in self.opt.models_to_load:
            print("Loading {} weights...".format(n))
            path = os.path.join(self.opt.load_weights_folder, "{}.pth".format(n))
            model_dict = self.models[n].state_dict()
            pretrained_dict = torch.load(path)
            pretrained_dict = {k: v for k, v in pretrained_dict.items() if k in model_dict}
            model_dict.update(pretrained_dict)
            self.models[n].load_state_dict(model_dict)

        # loading adam state
        optimizer_load_path = os.path.join(self.opt.load_weights_folder, "adam.pth")
        if os.path.isfile(optimizer_load_path):
            print("Loading Adam weights")
            optimizer_dict = torch.load(optimizer_load_path)
            self.model_optimizer.load_state_dict(optimizer_dict)
        else:
            print("Cannot find Adam weights so Adam is randomly initialized")