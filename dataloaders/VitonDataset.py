import os
import json

import cv2
from PIL import Image, ImageDraw
import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset
from torchvision import transforms

from densepose_utils import DenseposeSegmenter

semantic_cloth_labels = [
    [128, 0, 128], # upper
    [128, 128, 64], # left arm
    [128, 128, 192], # right arm
    [0, 255, 0], # neck
    [0, 128, 128], # dress
    [128, 128, 128], 
    
    [0, 0, 0], # bg
    
    [0, 128, 0], # hair
    [0, 64, 0], # left leg?
    [128, 128, 0], # right hand
    [0, 192, 0], # left foot
    [128, 0, 192], # head
    [0, 0, 192], # legs / skirt?
    [0, 64, 128], # skirt?
    [128, 0, 64], # left hand
    [0, 192, 128], # right foot
    [0, 0, 128],
    [0, 128, 64],
    [0, 0, 64],
    [0, 128, 192]
]

semantic_densepose_labels = [
    [0, 0, 0], # Background
	[37, 60, 163], # Torso back
	[20, 80, 194], # Torso front
	[4, 97, 223], # Right Hand
	[8, 110, 221], # Left Hand
	[14, 122, 216], # Left Foot
	[20, 132, 212], # Right Foot
	[15, 144, 208], # Upper Leg Right back
	[11, 156, 203], # Upper Leg Left back
	[6, 166, 198], # Upper Leg Right front
	[22, 173, 185], # Upper Leg Left front
	[38, 179, 172], # Lower Leg Right back
	[55, 185, 159], # Lower Leg Left back
	[86, 187, 144], # Lower Leg Right front
	[114, 189, 130], # Lower Leg Left front
	[145, 191, 116], # Upper Arm Left inner
	[170, 189, 105], # Upper Arm Right inner
	[192, 188, 96], # Upper Arm Left outer
	[216, 186, 86], # Upper Arm Right outer
	[228, 192, 74], # Lower Arm Left inner
	[240, 199, 60], # Lower Arm Right inner
	[252, 206, 46], # Lower Arm Left outer
	[251, 220, 36], # Lower Arm Right outer
	[250, 235, 25], # Head right
	[249, 251, 14] # Head left
]

semantic_body_labels = [
    [127, 127, 127],
    [0, 255, 255],
    [255, 255, 0],
    [127, 127, 0],
    [255, 127, 127],
    [0, 255, 0],
    [0, 0, 0],
    [255, 127, 0],
    [0, 0, 255],
    [127, 255, 127],
    [0, 127, 255],
    [127, 0, 255],
    [255, 255, 127],
    [255, 0, 0],
    [255, 0, 255]
]


class VitonDataset(Dataset):
    
    def __init__(self, opt, phase, dp_segmenter: DenseposeSegmenter = None, test_pairs=None):

        opt.label_nc = [len(semantic_body_labels), len(semantic_cloth_labels), len(semantic_densepose_labels)]
        opt.semantic_nc = [label_nc + 1 for label_nc in opt.label_nc]
        
        opt.offsets = [0]
        segmentation_modes = ["body", "cloth", "densepose"]
        for i, mode in enumerate(segmentation_modes):
            if mode in opt.segmentation:
                opt.offsets.append(opt.offsets[-1] + opt.semantic_nc[i] + 1) # to account for extra real/fake class in discriminator output
            else:
                opt.offsets.append(0)
        
        if isinstance(opt.img_size, int):
            opt.img_size = (opt.img_size, int(opt.img_size * 0.75))
        
        self.opt = opt
        self.phase = phase
        self.db_path = opt.dataroot
        
        test_pairs = "viton_%s_pairs.txt" % ("test" if phase in {"test", "test_same"} else "train") if test_pairs is None else test_pairs
        # test_pairs = "/home/benjamin/StyleTON/data/viton/viton_train_swap.txt"
        self.filepath_df = pd.read_csv(os.path.join(self.db_path, test_pairs), sep=" ", names=["poseA", "target"])
        # self.filepath_df.target = self.filepath_df.target.str.replace("_0", "_1")
        if phase == "test_same":
            self.filepath_df.target = self.filepath_df.poseA.str.replace("_0", "_1")
        
        if phase == "train":
            self.filepath_df = self.filepath_df.iloc[:int(len(self.filepath_df) * opt.train_size)]
        elif phase == "val":
            self.filepath_df = self.filepath_df.iloc[-int(len(self.filepath_df) * opt.val_size):]
        
        self.transform = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize(opt.img_size),
            transforms.ToTensor(),
        ])
        
        if phase in {"train", "train_whole"} and self.opt.add_pd_loss:
            self.hand_indices = [2, 7, 11, 14]
            self.body_label_centroids = [None] * len(self.filepath_df)
        else:
            self.body_label_centroids = None
        
        if dp_segmenter is None:
            self.dp_segmenter = DenseposeSegmenter("densepose_utils/densepose_rcnn_R_50_FPN_s1x.yaml") 
        else:
            self.dp_segmenter = dp_segmenter
            
    
    def __getitem__(self, index):
        df_row = self.filepath_df.iloc[index]

        # get original image of person
        image = cv2.imread(os.path.join(self.db_path, "data", "image", df_row["poseA"]))
        
        if "densepose" in self.opt.segmentation:
            densepose_seg_transf = self.dp_segmenter(image)
            densepose_seg_transf = cv2.resize(densepose_seg_transf, self.opt.img_size[::-1], 
                                              interpolation=cv2.INTER_NEAREST)
            densepose_seg_transf = np.expand_dims(densepose_seg_transf, 0)
            densepose_seg_transf = torch.tensor(densepose_seg_transf)
        else:
            densepose_seg_transf = torch.tensor([]) 
        
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image = cv2.resize(image, self.opt.img_size[::-1], interpolation=cv2.INTER_AREA)
        
        original_size = image.shape[:2]
        
        # extract non-warped cloth
        cloth_image = cv2.imread(os.path.join(self.db_path, "data", "cloth", df_row["target"]))
        cloth_image = cv2.cvtColor(cloth_image, cv2.COLOR_BGR2RGB)

        # mask the image to get desired inputs
        
        # get the mask without upper clothes / dress, hands, neck
        # additionally, get cloth segmentations by cloth part

        if "cloth" in self.opt.segmentation:
            # load cloth labels
            cloth_seg = cv2.imread(os.path.join(self.db_path, "data", "image_parse_with_hands", df_row["poseA"].replace(".jpg", ".png")))
            cloth_seg = cv2.cvtColor(cloth_seg, cv2.COLOR_BGR2RGB)
            cloth_seg = cv2.resize(cloth_seg, self.opt.img_size[::-1], interpolation=cv2.INTER_NEAREST)
            
            cloth_seg_transf = np.zeros(self.opt.img_size)
            mask = np.zeros(self.opt.img_size)
            for i, color in enumerate(semantic_cloth_labels):
                cloth_seg_transf[np.all(cloth_seg == color, axis=-1)] = i
                if i < (6 + self.opt.no_bg):    # this works, because colors are sorted in a specific way with background being the 8th.
                    mask[np.all(cloth_seg == color, axis=-1)] = 1.0
                    
            cloth_seg_transf = np.expand_dims(cloth_seg_transf, 0)
            cloth_seg_transf = torch.tensor(cloth_seg_transf)
          
        else: 
            cloth_seg_transf = torch.tensor([])
            mask = cv2.imread(os.path.join(self.db_path, "data", "mask", df_row["poseA"].replace(".jpg", ".png")),
                                cv2.IMREAD_GRAYSCALE)
            mask[mask>0] = 1
            mask = cv2.resize(mask, self.opt.img_size[::-1], interpolation=cv2.INTER_NEAREST)
          
          
        mask = np.repeat(np.expand_dims(mask, -1), 3, axis=-1).astype("uint8")
        masked_image = image * (1 - mask)
    
        # load and process the body labels
        
        if "body" in self.opt.segmentation:
            body_seg = cv2.imread(os.path.join(self.db_path, "data", "image_body_parse", df_row["poseA"].replace(".jpg", ".png")))
            body_seg = cv2.cvtColor(body_seg, cv2.COLOR_BGR2RGB)
            body_seg = cv2.resize(body_seg, self.opt.img_size[::-1], interpolation=cv2.INTER_NEAREST)
            
            # get one-hot encoded body segmentations 
            body_seg_transf = np.zeros(self.opt.img_size)
            for i, color in enumerate(semantic_body_labels):
                body_seg_transf[np.all(body_seg == color, axis=-1)] = i
                
                # additionally, get body segmentation centroids.
                if self.phase == "train" and self.opt.add_pd_loss and (self.body_label_centroids[index] is None or len(self.body_label_centroids[index]) != len(self.hand_indices)) and i in self.hand_indices:
                    if self.body_label_centroids[index] is None:
                        self.body_label_centroids[index] = []
                        
                    non_zero = np.nonzero(np.all(body_seg == color, axis=-1))
                    if len(non_zero[0]):
                        x = int(non_zero[0].mean())
                    else:
                        x = -1
                        
                    if len(non_zero[1]):
                        y = int(non_zero[1].mean())
                    else:
                        y = -1
                        
                    self.body_label_centroids[index].append([x, y])
                    
            body_label_centroid = self.body_label_centroids[index] if self.body_label_centroids is not None else ""
            
            body_seg_transf = np.expand_dims(body_seg_transf, 0)
            body_seg_transf = torch.tensor(body_seg_transf)
        else:
            body_seg_transf = torch.tensor([])
            body_label_centroid = torch.tensor([])
                    
        # scale the inputs to range [-1, 1]
        image = self.transform(image)
        image = (image - 0.5) / 0.5
        masked_image = self.transform(masked_image)
        masked_image = (masked_image - 0.5) / 0.5
        cloth_image = self.transform(cloth_image)
        cloth_image = (cloth_image - 0.5) / 0.5

        if self.opt.bpgm_id.find("old") >= 0:
            # load pose points
            pose_name = df_row["poseA"].replace('.jpg', '_keypoints.json')
            with open(os.path.join(self.db_path, "data", 'pose', pose_name), 'r') as f:
                try:
                    pose_label = json.load(f)
                    pose_data = pose_label['people'][0]['pose_keypoints_2d']
                    pose_data = np.array(pose_data)
                    pose_data = pose_data.reshape((-1,3))
                
                except IndexError:
                    pose_data = np.zeros((25, 3))

            pose_data[:, 0] = pose_data[:, 0] * (self.opt.img_size[0] / 1024)
            pose_data[:, 1] = pose_data[:, 1] * (self.opt.img_size[1] / 768)
            
            point_num = pose_data.shape[0]
            pose_map = torch.zeros(point_num, *self.opt.img_size)
            r = 5
            im_pose = Image.new('L', self.opt.img_size)
            pose_draw = ImageDraw.Draw(im_pose)
            for i in range(point_num):
                one_map = Image.new('L', self.opt.img_size)
                draw = ImageDraw.Draw(one_map)
                pointx = pose_data[i,0]
                pointy = pose_data[i,1]
                if pointx > 1 and pointy > 1:
                    draw.rectangle((pointx-r, pointy-r, pointx+r, pointy+r), 'white', 'white')
                    pose_draw.rectangle((pointx-r, pointy-r, pointx+r, pointy+r), 'white', 'white')
                
                one_map = self.transform(np.array(one_map))
                pose_map[i] = one_map[0]
                   
            # save background-person mask
            shape = torch.tensor(1 - np.all(cloth_seg == [0, 0, 0], axis=2).astype(np.float32)) * 2 - 1
            shape = shape.unsqueeze(0)
            
            # extract just the head image
            head_label_colors = [0, 128, 0], [128, 0, 192]
            
            head_mask = torch.zeros(self.opt.img_size)
            for color in head_label_colors:
                head_mask += np.all(cloth_seg == color, axis=2)
            
            im_h = image * head_mask
                
            # cloth-agnostic representation
            agnostic = torch.cat([shape, im_h, pose_map], 0).float()
        else:
            agnostic = ""

        return {"image": {"I": image,
                        "C_t": cloth_image,
                        "I_m": masked_image },
                "cloth_label": cloth_seg_transf,
                "body_label": body_seg_transf,
                "densepose_label": densepose_seg_transf,
                "name": df_row["poseA"],
                "agnostic": agnostic,
                "original_size": original_size,
                "label_centroid": body_label_centroid}          
          
    def __len__(self):
        return len(self.filepath_df)
    
    def name(self):
        return "VitonDataset"
