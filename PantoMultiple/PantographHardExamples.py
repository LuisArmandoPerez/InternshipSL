from os.path import join, exists

from keras.optimizers import Adam
from os import makedirs
from scipy.signal import convolve2d
import numpy as np
from PantographNetwork import PantographNetwork
from PantographNetwork import PantographNetwork
from Asbestos_Utils import load_image_label, get_file_extension, save_names
from DataPantograph import DataPantograph
from UNET_Model import jaccard_coef, dice_coef
import cv2

def pad_image(num_layers,image):
    sizex = image.shape[1]
    sizey = image.shape[0]
    max_pooling_layers = num_layers-1
    mod = 2**max_pooling_layers
    print('Mod',mod)
    print('Size',image.shape)
    if (sizex % mod) == 0:
        extrapixX =0
    else:
        extrapixX = mod - (sizex % mod)
    if (sizey % mod) == 0:
        extrapixY = 0
    else:
        extrapixY = mod - (sizey % mod)
    print('Extrapix',(extrapixX,extrapixY))
    left = extrapixX//2
    right = extrapixX-left
    top = extrapixY//2
    bottom = extrapixY-top
    image = cv2.copyMakeBorder(image, top, bottom, left, right, 0,value = [255,255,255])
    print('Image size ',image.shape)
    return image

class PantographHardExamples(object):
    def __init__(self, path_name, data_name, size, num_layers,classes,channels,
                 data_augmentation=False,
                 train_percentage=0.63, val_percentage=0.07, test_percentage=0.3,
                 file_type_load='.jpg',
                 loss="binary_crossentropy", optimizer=Adam(), data_type=".tiff",augmentation = False, batch_normalization = False,dropout_type = 0,dropout_p=1.0, hard_example_epochs = 0, hard_examples_per_image = 0):
        self.num_layers = num_layers
        self.data = DataPantograph(path_name, data_name, size, data_augmentation=data_augmentation,
                                 train_percentage=train_percentage, val_percentage=val_percentage,
                                 test_percentage=test_percentage,
                                 load_extension=file_type_load, augmentation= augmentation)
        self.UNETnet = PantographNetwork(DataPantograph=self.data,
                                    channels=channels,
                                    classes=classes,
                                    initial_features=32,
                                    num_layers=num_layers,
                                    loss=loss,
                                    optimizer=optimizer,
                                    metrics=[jaccard_coef, dice_coef],
                                    data_type=data_type,batch_normalization = batch_normalization, dropout_type=dropout_type, dropout_p=dropout_p)

        #self.models = AsbestosModels('./' + self.data.data_name)
        #self.models.print_available_models()
        self.model_name = []

    def get_previous_model(self, number):
        self.UNETnet.model, self.model_name = self.models.extract_model(number)

    def hard_example_training(self, prefix_log, HE_per_image=2, batch_size_train=10, batch_size_val=10,
                              epochs_per_training=50, hard_example_epochs=2):
        self.UNETnet.params['HEE'] = hard_example_epochs
        self.UNETnet.params['HEPI'] = HE_per_image
        if self.model_name == []:
            self.UNETnet.train_model(prefix_log=prefix_log, batch_size_train=batch_size_train,
                                     batch_size_val=batch_size_val,
                                     epochs=epochs_per_training)
        for hard_example_iteration in range(hard_example_epochs):
            self.iteration = hard_example_iteration
            self.hard_example_generator_convolution(HE_per_image)
            self.UNETnet.train_model(prefix_log + '/HE' + str(hard_example_iteration),
                                     batch_size_train=batch_size_train,
                                     batch_size_val=batch_size_val, epochs=epochs_per_training)



    def hard_example_generator_convolution(self, HE_per_image=2):
        '''
        This function reads the images in the read path and produces a prediction with the input model.
        The region of certain size with the most FP errors is identified.
        The crops are evaluated with the model and then HEpi crops with the worst score are then saved into the save_path
        '''
        print("Hard example generation started...\n")
        # Read the image names in the read_path
        names = self.data.train_names_original
        hard_examples = []
        for num_name, name in enumerate(names):
            print("Generating HE for image %d\n" % num_name)
            # Load the image with the corresponding labels
            print(self.data.path_name,name)
            img = cv2.imread(join(self.data.path_name,name))
            label = cv2.imread(join(self.data.path_name,name.replace(get_file_extension(name),'_FIB'+get_file_extension(name))),0)
            img = pad_image(self.num_layers,img)
            label = pad_image(self.num_layers,label)
            #img, label = load_image_label(self.data.path_name, name)
            label_thresh = (label == 255).astype(np.int)
            # Create the predictions from the original image
            prediction = self.UNETnet.model.predict(img[np.newaxis, :, :])
            # Compare the prediction with the ground truth
            print('Shape prediction',prediction.shape,'Label',label_thresh.shape)
            comparison = np.multiply((1 - prediction[0, :, :, 0]), label_thresh)
            print('Comparison',comparison.shape)
            # Make the convolution to find the left top corner for the hard example
            hard_example_names = self.convolutional_selection(comparison, img, label, name, HE_per_image)
            for name_hard in hard_example_names:
                hard_examples.append(name_hard)
        if not(exists('./'+self.data.data_name+'/Hard Examples')):
            makedirs('./'+self.data.data_name+'/Hard Examples')
        save_names(hard_examples,'./' + self.data.data_name +'/Hard Examples/', self.UNETnet.model_name + " Iter " + str(self.iteration))

    def convolutional_selection(self, comparison, img, label, name, HE_per_image):
        # Obtain the left top corner for the hard example window
        hard_example_names = []
        convolved = convolve2d(comparison, np.ones((self.data.size[1], self.data.size[0])), mode='valid')
        for num_example in range(HE_per_image):
            index1, index2 = np.unravel_index(convolved.argmax(), convolved.shape)
            hard_example = img[index1:(index1 + self.data.size[1]),
                           index2:(index2 + self.data.size[0])]  # Get the hard example window
            hard_example_label = label[index1:(index1 + self.data.size[1]),
                                 index2:(index2 + self.data.size[0])]  # Get the hard example label window

            # Remove a certain search area for the convolution
            index1_rmv_conv_start = max([index1 - int(self.data.size[1] / 4) + 1, 0])
            index2_rmv_conv_start = max([index2 - int(self.data.size[0] / 4) + 1, 0])
            index1_rmv_conv_end = min([index1_rmv_conv_start + (self.data.size[1] - 2), img.shape[0]])
            index2_rmv_conv_end = min([index2_rmv_conv_start + (self.data.size[0] - 2), img.shape[1]])
            convolved[index1_rmv_conv_start:index1_rmv_conv_end, index2_rmv_conv_start:index2_rmv_conv_end] = 0

            save_name = name.replace(get_file_extension(name),
                                     '_HEP_' + str(index1)+ '_' + str(index2) + self.UNETnet.data_type)
            hard_example_names.append(save_name)
            cv2.imwrite(join(self.data.temp_train, save_name), hard_example)
            cv2.imwrite(
                join(self.data.temp_train, save_name.replace(get_file_extension(save_name), '_FIB' + get_file_extension(save_name))),
                hard_example_label)
            self.UNETnet.train_names.append(save_name)
            print("HE %d created \n" % num_example)
        return hard_example_names