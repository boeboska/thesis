# Copyright Niantic 2019. Patent Pending. All rights reserved.
#
# This software is licensed under the terms of the Monodepth2 licence
# which allows for non-commercial use only, the full terms of which are made
# available in the LICENSE file.

from __future__ import absolute_import, division, print_function

import os
import argparse
import seaborn as sns; sns.set_theme()
import seaborn as sns

file_dir = os.path.dirname(__file__)  # the directory that options.py resides in


class MonodepthOptions:
    def __init__(self):
        self.parser = argparse.ArgumentParser(description="Monodepthv2 options")



        # ATTENTION
        self.parser.add_argument("--edge_loss",
                                 type=bool,
                                 help="True if you want to train with the edge loss which says that depth estimate "
                                      "should be smooth withing an attention mask",
                                 default=False)
        self.parser.add_argument("--attention_mask_loss",
                                 type=bool,
                                 help="True if you want give more weight in SSIM and L1 on the attention mask pixels",
                                 default=False)
        self.parser.add_argument("--attention_path",
                                 type=str,
                                 help="path to the attention masks data",
                                 default="../../../attention_masks_hidde/")
        self.parser.add_argument("--weight_matrix_path",
                                 type=str,
                                 help="path to the attention masks data",
                                 default="../../../weight_mask/")
        self.parser.add_argument("--attention_threshold",
                                 type=float,
                                 help="how accurate the attention maps should be",
                                 default = 0.5)
        self.parser.add_argument("--save_plot_every",
                                 type=int,
                                 help="how often to save edge loss or additional weight loss images during training",
                                 default=500)
        self.parser.add_argument("--edge_detection_threshold",
                                 type=float,
                                 help="The threshold used for canny edge detection. The lower the number the easier it will find edges",
                                 default=0.1)
        self.parser.add_argument("--seed",
                                 type=float,
                                 help="The random seed used for experiments",
                                 default=4)
        self.parser.add_argument("--attention_mask_threshold",
                                 type=float,
                                 help="All values up to this number are mapped back to 1",
                                 default=1.05)
        self.parser.add_argument("--convolution_experiment",
                                 type=bool,
                                 help="Perform convolutions over the attention masks",
                                 default=False)
        self.parser.add_argument("--top_k",
                                 type=int,
                                 help="Load in the best k amount of attention masks when convolution experiment is true. number between 0 - 100",
                                 default=0)
        self.parser.add_argument("--early_stop_percentage",
                                 type = float,
                                 help= "the last three validation steps must differ less then this percentage for early stopping",
                                 default = 0.00000001)


        self.parser.add_argument("--weight_mask_method",
                                 type=str,
                                 help="The type of weight mask it will load in",
                                 choices=["avg", "min", "max"],
                                 default='avg')

        self.parser.add_argument("--attention_weight",
                                 type=float,
                                 help="attention depth weight",
                                 default=5)
        self.parser.add_argument("--reduce_attention_weight",
                                 type=float,
                                 help="map attention weight numbers to this number based on filter rule",
                                 default=0.9)


        self.parser.add_argument("--edge_weight",
                                 type=float,
                                 help="Weight multiplied with the edge loss",
                                 default=2e-4)
        self.parser.add_argument("--attention_sum",
                                 type = int,
                                 help = "threshold of how big the attention mask may be during training",
                                 default = 12500)

        self.parser.add_argument("--labels_inside_mask",
                                 type = bool,
                                 help = "Only calculate the labels inside the attention masks",
                                 default = False)

        # PATHS
        self.parser.add_argument("--data_path",
                                 type=str,
                                 help="path to the training data",
                                 default="../../../kitti/")
                                 # default=os.path.join(file_dir, "kitti_data"))
        self.parser.add_argument("--log_dir",
                                 type=str,
                                 help="log directory",
                                 default="monodepth_models/")
                                 # default=os.path.join(os.path.expanduser("~"), "tmp"))
        self.parser.add_argument('--image_path',
                                 type=str,
                                 help='path to a test image or folder of images', required=False)

        # TRAINING options
        self.parser.add_argument("--model_name",
                                 type=str,
                                 help="the name of the folder to save the model in",
                                 default="mdp")
        self.parser.add_argument("--split",
                                 type=str,
                                 help="which training split to use",
                                 choices=["eigen_zhou", "eigen_full", "odom", "benchmark", "short"],
                                 default="eigen_zhou")
        self.parser.add_argument("--num_layers",
                                 type=int,
                                 help="number of resnet layers",
                                 default=18,
                                 choices=[18, 34, 50, 101, 152])
        self.parser.add_argument("--dataset",
                                 type=str,
                                 help="dataset to train on",
                                 default="kitti",
                                 choices=["kitti", "kitti_odom", "kitti_depth", "kitti_test"])
        self.parser.add_argument("--png",
                                 help="if set, trains from raw KITTI png files (instead of jpgs)",
                                 action="store_true")
        self.parser.add_argument("--height",
                                 type=int,
                                 help="input image height",
                                 default=192)
        self.parser.add_argument("--width",
                                 type=int,
                                 help="input image width",
                                 default=640)
        self.parser.add_argument("--disparity_smoothness",
                                 type=float,
                                 help="disparity smoothness weight",
                                 default=1e-3)
        self.parser.add_argument("--scales",
                                 nargs="+",
                                 type=int,
                                 help="scales used in the loss",
                                 default=[0, 1, 2, 3])
        self.parser.add_argument("--min_depth",
                                 type=float,
                                 help="minimum depth",
                                 default=0.1)
        self.parser.add_argument("--max_depth",
                                 type=float,
                                 help="maximum depth",
                                 default=100.0)
        self.parser.add_argument("--use_stereo",
                                 help="if set, uses stereo pair for training",
                                 action="store_true")
        self.parser.add_argument("--frame_ids",
                                 nargs="+",
                                 type=int,
                                 help="frames to load",
                                 default=[0, -1, 1])

        # OPTIMIZATION options
        self.parser.add_argument("--batch_size",
                                 type=int,
                                 help="batch size",
                                 default=1)
        self.parser.add_argument("--learning_rate",
                                 type=float,
                                 help="learning rate",
                                 default=1e-4)
        self.parser.add_argument("--num_epochs",
                                 type=int,
                                 help="number of epochs",
                                 default=20)
        self.parser.add_argument("--scheduler_step_size",
                                 type=int,
                                 help="step size of the scheduler",
                                 default=15)

        # self attention

        self.parser.add_argument("--self_attention",
                                 help="if set, uses self-attention",
                                 type=bool,
                                 default = False)
        # self.parser.add_argument("--no_ddv",
        #                          help="is set, disable discrete disparity volume",
        #                          action="store_true",
        #                          default = True)

        # ABLATION options
        self.parser.add_argument("--v1_multiscale",
                                 help="if set, uses monodepth v1 multiscale",
                                 action="store_true")
        self.parser.add_argument("--avg_reprojection",
                                 help="if set, uses average reprojection loss",
                                 action="store_true")
        self.parser.add_argument("--disable_automasking",
                                 help="if set, doesn't do auto-masking",
                                 action="store_true")
        self.parser.add_argument("--predictive_mask",
                                 help="if set, uses a predictive masking scheme as in Zhou et al",
                                 action="store_true")
        self.parser.add_argument("--no_ssim",
                                 help="if set, disables ssim in the loss",
                                 action="store_true")
        self.parser.add_argument("--weights_init",
                                 type=str,
                                 help="pretrained or scratch",
                                 default="pretrained",
                                 choices=["pretrained", "scratch"])
        self.parser.add_argument("--pose_model_input",
                                 type=str,
                                 help="how many images the pose network gets",
                                 default="pairs",
                                 choices=["pairs", "all"])
        self.parser.add_argument("--pose_model_type",
                                 type=str,
                                 help="normal or shared",
                                 default="separate_resnet",
                                 choices=["posecnn", "separate_resnet", "shared"])

        # SYSTEM options
        self.parser.add_argument("--no_cuda",
                                 help="if set disables CUDA",
                                 action="store_true")
        self.parser.add_argument("--num_workers",
                                 type=int,
                                 help="number of dataloader workers",
                                 default=1)

        # LOADING options
        self.parser.add_argument("--load_weights_folder",
                                 type=str,
                                 help="name of model to load")
        self.parser.add_argument("--models_to_load",
                                 nargs="+",
                                 type=str,
                                 help="models to load",
                                 default=["encoder", "depth", "pose_encoder", "pose"])

        # LOGGING options
        self.parser.add_argument("--log_frequency",
                                 type=int,
                                 help="number of batches between each tensorboard log",
                                 default=250)
        self.parser.add_argument("--save_frequency",
                                 type=int,
                                 help="number of epochs between each save",
                                 default=1)

        # EVALUATION options
        self.parser.add_argument("--eval_stereo",
                                 help="if set evaluates in stereo mode",
                                 action="store_true")
        self.parser.add_argument("--eval_mono",
                                 help="if set evaluates in mono mode",
                                 action="store_true")
        self.parser.add_argument("--disable_median_scaling",
                                 help="if set disables median scaling in evaluation",
                                 action="store_true")
        self.parser.add_argument("--pred_depth_scale_factor",
                                 help="if set multiplies predictions by this number",
                                 type=float,
                                 default=1)
        self.parser.add_argument("--ext_disp_to_eval",
                                 type=str,
                                 help="optional path to a .npy disparities file to evaluate")
        self.parser.add_argument("--eval_split",
                                 type=str,
                                 default="eigen",
                                 choices=[
                                    "eigen", "eigen_benchmark", "benchmark", "odom_9", "odom_10"],
                                 help="which split to run eval on")
        self.parser.add_argument("--save_pred_disps",
                                 help="if set saves predicted disparities",
                                 action="store_true")
        self.parser.add_argument("--no_eval",
                                 help="if set disables evaluation",
                                 action="store_true")
        self.parser.add_argument("--eval_eigen_to_benchmark",
                                 help="if set assume we are loading eigen results from npy but "
                                      "we want to evaluate using the new benchmark.",
                                 action="store_true")
        self.parser.add_argument("--eval_out_dir",
                                 help="if set will output the disparities to this folder",
                                 type=str)
        self.parser.add_argument("--post_process",
                                 help="if set will perform the flipping post processing "
                                      "from the original monodepth paper",
                                 action="store_true")

    # def parse(self):
    #     self.options = self.parser.parse_args()
    #     return self.options


    def parse(self):
        self.options = self.parser.parse_args()

        return self.options




        # print("###################")
        #
        # # based on the current threshold determine how many attention masks you want to use
        # # during training. This is based on a table where we calculated the mean amount of
        # # maps per threshold. This saves a lot of computational speed as you don't have to
        # # load all the 100 attention maps to your gpu per image but only a small amount
        #
        # threshold = self.options.attention_threshold
        #
        # amount_of_masks = {
        #     0.9: 12,
        #     0.8: 15,
        #     0.7: 18,
        #     0.6: 23,
        #     0.5: 30,
        #     0.4: 35,
        #     0.3: 40,
        #     0.2: 50
        # }
        #
        # mask_amount = amount_of_masks[threshold]
        #
        # print("MASK AMOUNT", mask_amount)
        #
        # self.parser.add_argument("--mask_amount",
        #                          help="amount of masks during training. dependend of the attention threshold."
        #                               "This saves a lot of computational speed",
        #                          default=mask_amount)

        # self.options = self.parser.parse_args()

