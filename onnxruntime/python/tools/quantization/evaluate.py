#!/usr/bin/env python
# coding: utf-8
# -------------------------------------------------------------------------
# Copyright (c) Microsoft, Intel Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------

from .calibrate import CalibrationDataReader, calibrate
import onnxruntime
import numpy as np

class YoloV3Evaluator: 
    def __init__(self, model_path,
                       data_reader: CalibrationDataReader,
                       width=416,
                       height=416,
                       providers=["CUDAExecutionProvider"],
                       ground_truth_object_class_file="./coco-object-categories-2017.json",
                       onnx_object_class_file="./onnx_coco_classes.txt"):
        '''
        :param model_path: ONNX model to validate 
        :param data_reader: user implemented object to read in and preprocess calibration dataset
                            based on CalibrationDataReader Interface

        '''
        self.model_path = model_path
        self.data_reader = data_reader
        self.width = width
        self.height = height
        self.providers = providers 
        self.class_to_id = {} # object class -> id
        self.onnx_class_list = []
        self.prediction_result_list = []
        self.identical_class_map = {"motorbike": "motorcycle", "aeroplane": "airplane", "sofa": "couch", "pottedplant": "potted plant", "diningtable": "dining table", "tvmonitor": "tv"}

        f = open(onnx_object_class_file, 'r')
        lines = f.readlines()
        for c in lines:
            self.onnx_class_list.append(c.strip('\n'))

        self.generate_class_to_id(ground_truth_object_class_file)
        print(self.class_to_id)


    def generate_class_to_id(self, ground_truth_object_class_file):
        with open(ground_truth_object_class_file) as f:
            import json
            classes = json.load(f)

        for c in classes:
            self.class_to_id[c["name"]] = c["id"]

    def set_data_reader(self, data_reader):
        self.data_reader = data_reader

    def get_result(self):
        return self.prediction_result_list

    def predict(self):
        session = onnxruntime.InferenceSession(self.model_path, providers=self.providers)

        outputs = []
        while True:
            inputs = self.data_reader.get_next()
            if not inputs:
                break

            image_id = inputs["image_id"]
            del inputs["image_id"]

            output = session.run(None, inputs)
            outputs.append(output)

            out_boxes, out_scores, out_classes = [], [], []
            boxes = output[0]
            scores = output[1]
            indices = output[2]

            # print(boxes)
            # print(scores)
            # print(indices)

            for idx_ in indices:
                out_classes.append(idx_[1])
                out_scores.append(scores[tuple(idx_)])
                idx_1 = (idx_[0], idx_[2])
                out_boxes.append(boxes[idx_1])

            # print(out_classes)
            # print(out_scores)
            # print(out_boxes)


            for i in range(len(out_classes)):
                out_class = out_classes[i]
                class_name = self.onnx_class_list[int(out_class)]
                if class_name in self.identical_class_map:
                    class_name = self.identical_class_map[class_name]
                id = self.class_to_id[class_name]

                bbox = [out_boxes[i][1], out_boxes[i][0], out_boxes[i][3], out_boxes[i][2]]
                bbox_yxhw = [out_boxes[i][1], out_boxes[i][0], out_boxes[i][3]-out_boxes[i][1], out_boxes[i][2]-out_boxes[i][0]]
                bbox_yxhw_str = [str(out_boxes[i][1]), str(out_boxes[i][0]), str(out_boxes[i][3]-out_boxes[i][1]), str(out_boxes[i][2]-out_boxes[i][0])]
                score = str(out_scores[i])
                coor = np.array(bbox[:4], dtype=np.int32)
                c1, c2 = (coor[0], coor[1]), (coor[2], coor[3])
                
                # print("bbox_yxhw")
                # print(bbox_yxhw)
                self.prediction_result_list.append({"image_id":int(image_id), "category_id":int(id), "bbox":bbox_yxhw, "score":out_scores[i]})

    def evaluate(self, prediction_result, annotations):
        # calling coco api
        from pycocotools.coco import COCO
        from pycocotools.cocoeval import COCOeval
        import numpy as np
        import skimage.io as io
        import pylab
        pylab.rcParams['figure.figsize'] = (10.0, 8.0)


        annType = ['segm','bbox','keypoints']
        annType = annType[1]      #specify type here
        prefix = 'person_keypoints' if annType=='keypoints' else 'instances'
        print('Running evaluation for *%s* results.'%(annType))

        annFile = annotations
        cocoGt=COCO(annFile)

        resFile = prediction_result 
        cocoDt=cocoGt.loadRes(resFile)

        imgIds=sorted(cocoGt.getImgIds())
        imgIds=imgIds[0:100]
        imgId = imgIds[np.random.randint(100)]


        # running evaluation
        cocoEval = COCOeval(cocoGt,cocoDt,annType)
        cocoEval.params.imgIds  = imgIds
        cocoEval.evaluate()
        cocoEval.accumulate()
        cocoEval.summarize()

class YoloV3VisionEvaluator(YoloV3Evaluator): 
    def __init__(self, model_path,
                       data_reader: CalibrationDataReader,
                       width=608,
                       height=384,
                       providers=["CUDAExecutionProvider"],
                       ground_truth_object_class_file="./coco-object-categories-2017.json",
                       onnx_object_class_file="./onnx_coco_classes.txt"):

        YoloV3Evaluator.__init__(self, model_path, data_reader,width, height, providers, ground_truth_object_class_file, onnx_object_class_file)

    def scale_coords(self, img1_shape, coords, img0_shape, ratio_pad=None):
        # Rescale coords (xyxy) from img1_shape to img0_shape
        if ratio_pad is None:  # calculate from img0_shape
            gain = max(img1_shape) / max(img0_shape)  # gain  = old / new
            pad = (img1_shape[1] - img0_shape[1] * gain) / 2, (img1_shape[0] - img0_shape[0] * gain) / 2  # wh padding
        else:
            gain = ratio_pad[0][0]
            pad = ratio_pad[1]

        coords[[0, 2]] -= pad[0]  # x padding
        coords[[1, 3]] -= pad[1]  # y padding
        coords[:4] /= gain
        return coords

    def xywh2xyxy(self, x):
        # Convert nx4 boxes from [x, y, w, h] to [x1, y1, x2, y2] where xy1=top-left, xy2=bottom-right
        y = np.zeros_like(x)
        y[0] = x[0] - x[2] / 2  # top left x
        y[1] = x[1] - x[3] / 2  # top left y
        y[2] = x[0] + x[2] / 2  # bottom right x
        y[3] = x[1] + x[3] / 2  # bottom right y
        return y

    def predict(self):
        session = onnxruntime.InferenceSession(self.model_path, providers=self.providers)

        outputs = []
        while True:
            inputs = self.data_reader.get_next()
            if not inputs:
                break

            image_id = inputs["image_id"]
            image_width = inputs["image_width"]
            image_height = inputs["image_height"]
            del inputs["image_id"]
            del inputs["image_width"]
            del inputs["image_height"]

            output = session.run(None, inputs)
            outputs.append(output)

            bboxes = output[1]
            scores = output[2]
            

            for i in range(bboxes.shape[0]): 
                bbox = bboxes[i]
                bbox[0] *= self.width #x
                bbox[1] *= self.height #y
                bbox[2] *= self.width #w
                bbox[3] *= self.height #h
                
                img0_shape = (image_height, image_width)
                img1_shape = (self.height, self.width)
                bbox = self.xywh2xyxy(bbox)
                bbox = self.scale_coords(img1_shape, bbox, img0_shape)

                class_name = 'person' 
                if class_name in self.identical_class_map:
                    class_name = self.identical_class_map[class_name]
                id = self.class_to_id[class_name]

                bbox[2] = bbox[2] - bbox[0]
                bbox[3] = bbox[3] - bbox[1]

                self.prediction_result_list.append({"image_id":int(image_id), "category_id":int(id), "bbox":list(bbox), "score":scores[i][0]})
