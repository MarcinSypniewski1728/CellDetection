from ultralytics import YOLO
import cv2
import math
import random
import numpy as np

def random_crop(image, crop_size):
    """
    Randomly crop from an image in provided size

    image (np.array): image to crop
    crop_size (int): size of a cropped image

    return: cropped image with top and left coordinates
    """
    image_h, image_w = image.shape[:2]
    left = random.randint(0, image_w - crop_size)
    top = random.randint(0, image_h - crop_size)
    right = left + crop_size
    bottom = top + crop_size
    cropped_image = image[top:bottom, left:right]
    return cropped_image, top, left

class CellDetector:
    """
    Class to wrap cell detection YOLOv8 model

    model_path (str): path to the YOLOv8 model
    """
    def __init__(self, model_path):
        self.model = YOLO(model_path)
        self.IMAGE_SIZE = 128
        self.CROP_WIDTH = 128
        self.CROP_HEIGHT = 128

# |----------------------PREDICTIONS--------------------------------------------|

    def predict(self, image, conf, iou, device = None):
        """
        Predict boxes of died cells on a single image and postprocess the results

        image (np.array): image to predict
        conf (float): confidence threshold for predictions
        iou (float): IoU threshold for predictions
        device (str): device argument to pass to yolo predict function to choose which device (CPU/GPU) use for inference 
        for simple GPU usage use "device=0"

        return: list of predicted boxes with classes
        """
        if len(image.shape) < 3:
            image = cv2.merge((image,image,image))
        results = self.model.predict(image, conf=conf, iou=iou, imgsz=self.IMAGE_SIZE, device=device)
        results = self.process_results(results)
        return results

    def predict_with_crop(self, big_image, conf, iou, device = None, withOverlap = False, withImage = False):
        """
        Predict boxes if died cells on a single image. Predictions are made on a smaller cropped images from the original
        and then scaled to the original image.

        big_image (np.array): image to predict
        conf (float): confidence threshold for predictions
        iou (float): IoU threshold for predictions
        device (str): device argument to pass to yolo predict function to choose which device (CPU/GPU) use for inference.
        for simple GPU usage use "device=0"
        withOverlap (bool): if cropping should be with overlap or not
        withImage (bool): if should return processed image

        return: list of predicted boxes with classes (optionaly processed iamge)
        """
        if withOverlap:
            pass
        else:
            big_image, width_shift, height_shift = self.prepare_image_to_crop_no_overlap(big_image, withWinShift = True)

        image_h, image_w = big_image.shape[:2]
        num_width_windows = image_w/width_shift
        num_height_windows = image_h/height_shift

        results_all = []

        for width_window in range(int(num_width_windows)):
            for height_window in range(int(num_height_windows)):
                curr_topleft_x = width_window * width_shift
                curr_topleft_y = height_window * height_shift
                img_crop = big_image[curr_topleft_y:curr_topleft_y + self.CROP_HEIGHT, curr_topleft_x:curr_topleft_x + self.CROP_WIDTH]
                results = self.predict(img_crop, conf, iou, device)
                results = self.scale_crop_results(results, curr_topleft_x, curr_topleft_y)
                for result in results:
                    results_all.append(result)

        if withImage:
            return results_all, big_image
        else:
            return results_all

    def predict_with_heatmap(self, big_image, conf, iou, device = None, desired_coverage = 100, withImage = False, withResults = False):
        """
        Predict boxes of died cells on a single image. Predictions are made on a smaller random cropped images from the original.
        All the predictions are then converted to a heat map.

        big_image (np.array): image to predict
        conf (float): confidence threshold for predictions
        iou (float): IoU threshold for predictions
        device (str): device argument to pass to yolo predict function to choose which device (CPU/GPU) use for inference.
        for simple GPU usage use "device=0"
        desired_coverage (int): number of times to cover each pixel
        withImage (bool): if should return processed image
        withResults (bool): if should return list of result boxes

        return: heat map image (optionaly big_image and or list of results)
        """ 
        big_image = self.prepare_image_to_crop_heatmap(big_image)
        big_image_h, big_image_w = big_image.shape[:2]

        # prepare needed vars and calculate the number of crops needed
        print("Random Croping")
        total_crops = big_image_w * big_image_h * desired_coverage // (self.IMAGE_SIZE * self.IMAGE_SIZE)
        print("Total random crops to execute: " + str(total_crops))

        # heat map images
        apoptosis_image = np.zeros((big_image_h, big_image_w), dtype=np.int16)
        necroptosis_image = np.zeros((big_image_h, big_image_w), dtype=np.int16)
        background_image = np.zeros((big_image_h, big_image_w), dtype=np.int16)

        if withResults:
            results_all = []

        for _ in range(total_crops):
            img_crop_read, top, left = random_crop(big_image, self.IMAGE_SIZE)
            for rotation in [cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE, 3]:
                if rotation != 3:
                    img_crop_base = img_crop_read.copy()
                    img_crop = cv2.rotate(img_crop_base, rotation)
                else:
                    img_crop = img_crop_read.copy()

                results = self.predict(img_crop, conf, iou, device)
                results = self.rotate_results(results, rotation)

                if withResults:
                    results_scaled = self.scale_crop_results(results, left, top)
                    for result in results_scaled:
                        results_all.append(result)

                apoptosis_crop = np.zeros((self.IMAGE_SIZE, self.IMAGE_SIZE), dtype=np.int16)
                necroptosis_crop = np.zeros((self.IMAGE_SIZE, self.IMAGE_SIZE), dtype=np.int16)
                background_crop = np.ones((self.IMAGE_SIZE, self.IMAGE_SIZE), dtype=np.int16)

                apoptosis_crop, necroptosis_crop, background_crop = self.gen_masks(apoptosis_crop, necroptosis_crop, background_crop, results)

                apoptosis_image[top:top + self.IMAGE_SIZE, left:left + self.IMAGE_SIZE] += apoptosis_crop
                necroptosis_image[top:top + self.IMAGE_SIZE, left:left + self.IMAGE_SIZE] += necroptosis_crop
                background_image[top:top + self.IMAGE_SIZE, left:left + self.IMAGE_SIZE] += background_crop
        
        apoptosis_image = apoptosis_image.astype(np.float16)
        necroptosis_image = necroptosis_image.astype(np.float16)
        background_image = background_image.astype(np.float16)

        sum_image = apoptosis_image + necroptosis_image + background_image
        sum_image[sum_image < 1.0] = 1.0

        apoptosis_image = apoptosis_image/sum_image
        necroptosis_image = necroptosis_image/sum_image
        background_image = background_image/sum_image

        if withResults:
            if withImage:
                return apoptosis_image, necroptosis_image, background_image, big_image, results_all
            else:
                return apoptosis_image, necroptosis_image, background_image, results_all
        else:
            if withImage:
                return apoptosis_image, necroptosis_image, background_image, big_image
            else:
                return apoptosis_image, necroptosis_image, background_image

# |----------------------RESULT PROCESSING--------------------------------------------|

    def scale_crop_results(self, results, curr_topleft_x, curr_topleft_y):
        """
        Scale predicted boxes coordinates made on a cropped image to match the original image dimensions

        results (list(list)): list of predicted boxes with classes
        curr_topleft_x (int): x axis coordinate value of top left corner of the cropped image from the original
        curr_topleft_y (int): y axis coordinate value of top left corner of the cropped image from the original

        return: list of scaled predicted boxes with classes
        """
        new_results = []
        for r in results:
            new_result = [r[0] + curr_topleft_y, r[1] + curr_topleft_x, r[2] + curr_topleft_y, r[3] + curr_topleft_x, r[4]]
            new_results.append(new_result)
        return new_results

    def process_results(self, results):
        """
        Process YOLOv8 prediction to desired format [top, left, bottom, right, class]

        results (list(list)): list of predicted boxes with classes

        return: list of predicted boxes with classes in the desired format
        """
        results_list = []
        result = results[0]
        for box in result.boxes:
            cell_cls = int(box.cls)
            b_xyxy = box.xyxy[0]
            l = int(b_xyxy[0])
            t = int(b_xyxy[1])
            r = int(b_xyxy[2])
            b = int(b_xyxy[3])
            results_list.append([t,l,b,r,cell_cls])
        return results_list

    def rotate_results(self, results, rotation):
        """
        Rotate a results by a given angle around. Rotation is couterclokwise to revert results to original image position.

        results (list(list)): list of predicted boxes with classes to rotate
        rotation (int): rotation type by which to choose angle to rotate by.
        Possible rotation values:
         - 0 - 90 deg clockwise
         - 1 - 180 deg
         - 2 - 90 counterclokwise

        return: list of rotated results
        """
        # set angle in degrees, convert radians and add minus to rotate counterclockwise
        angle = (rotation * 90) + 90
        angle = -math.radians(angle)

        results_list = []

        for result in results:
            top = result[0]
            left = result[1]
            bottom = result[2]
            right = result[3]

            # middle of the image 
            oy = self.IMAGE_SIZE/2
            ox = self.IMAGE_SIZE/2

            left_r = int(ox + math.cos(angle) * (left - ox) - math.sin(angle) * (top - oy))
            top_r = int(oy + math.sin(angle) * (left - ox) + math.cos(angle) * (top - oy))
            right_r = int(ox + math.cos(angle) * (right - ox) - math.sin(angle) * (bottom - oy))
            bottom_r = int(oy + math.sin(angle) * (right - ox) + math.cos(angle) * (bottom - oy))

            results_list.append([top_r, left_r, bottom_r, right_r, result[4]])

        return results_list

    def gen_masks(self, apoptosis_image, necroptosis_image, background_image, results):
        for t, l ,b, r, r_cls in results:
            background_image[t:b, l:r] = 0
            # add values to the predicted pixels based on class
            if r_cls == 0:  # A Apoptosis
                apoptosis_image[t:b, l:r] +=1 
            elif r_cls == 1:  # Necroptosis
                necroptosis_image[t:b, l:r] +=1
        return apoptosis_image, necroptosis_image, background_image

# |----------------------PREPROCESSING--------------------------------------------|

    def prepare_image_to_crop_no_overlap(self, image, withWinShift = False):
        """
        Add padding if required to the image before crop it into equal parts

        image (np.array): image to add padding to
        withWinShift (bool): if to return shift values of croping window

        return: processed image (optionaly: shift values of croping window)
        """
        image_h, image_w = image.shape[:2]

        CROP_WIDTH_SHIFT = self.CROP_WIDTH
        CROP_HEIGHT_SHIFT = self.CROP_HEIGHT

        if image_w % self.CROP_WIDTH != 0:
            desired_w = math.ceil(image_w/self.CROP_WIDTH)
            left = ((desired_w - (image_w/self.CROP_WIDTH)) * 128)
            right = 0
        else:
            left = 0
            right = 0

        if image_h % self.CROP_HEIGHT != 0:
            desired_h = math.ceil(image_h/self.CROP_HEIGHT)
            top = int((desired_h - (image_h/self.CROP_HEIGHT)) * 128)
            bottom = 0
        else:
            top = 0
            bottom = 0

        image = cv2.copyMakeBorder(image, top, bottom, left, right, cv2.BORDER_CONSTANT)

        if withWinShift:
            return image, CROP_WIDTH_SHIFT, CROP_HEIGHT_SHIFT
        else:
            return image

    def prepare_image_to_crop_heatmap(self, image):
        """
        Add padding to the image before random cropping for heatmap to make sure to cover all parts of image in all positions

        image (np.array): image to add padding to

        return: processed image
        """
        top = 128
        bottom = 128
        left = 128
        right = 128

        image = cv2.copyMakeBorder(image, top, bottom, left, right, cv2.BORDER_CONSTANT)

        return image


# |----------------------VISUALIZATIONS--------------------------------------------|

    def box_image(self, image, boxes, image_green = None, prepare_type = 0):
        """
        Add prediction boxes of cells in specified colors to the image

        image (np.array): image to add boxes to
        boxes (list(list)): list of predicted boxes to add to the image
        image_green (np.array): additional image to add boxes to
        prepare_type (int): type of preparation to be done on green image
        Possible prepare_type values:
         - 0 - no overlap
         - 1 - padding for heatmap

        return: image with added boxes (optionaly: additional image with added boxes)
        """

        if image_green is not None:
            if prepare_type == 0:
                image_green = self.prepare_image_to_crop_no_overlap(image_green)
            elif prepare_type == 1:
                image_green = self.prepare_image_to_crop_heatmap(image_green)

            image_green_crop_3channel = cv2.merge((image_green,image_green,image_green))

        for box in boxes:
            t = box[0]
            l = box[1]
            b = box[2]
            r = box[3]

            cell_cls = box[4]
            if cell_cls == 0:
                color = (255,255,255)
                color_green = (255,50,100)
            elif cell_cls == 1:
                color = (0,0,0)
                color_green = (50,100,255)

            image = cv2.rectangle(image, (l,t), (r,b), color_green, 2)
            if image_green is not None:
                image_green_crop_3channel = cv2.rectangle(image_green_crop_3channel, (l,t), (r,b), color_green, 2)

        if image_green is not None:
            return image, image_green_crop_3channel
        else:
            return image