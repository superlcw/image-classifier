import numpy as np
import os
import cv2
import cv2.cv as cv
from skimage import transform as tf
from PIL import Image, ImageDraw
import threading
from time import ctime,sleep
import time
import sklearn
import matplotlib.pyplot as plt
import skimage
import sklearn.metrics.pairwise as pw
import triplet._init_paths
import triplet.config as cfg
from triplet.sampledata import sampledata
from utils.timer import Timer
import caffe
from caffe.proto import caffe_pb2
import google.protobuf as pb2
import argparse
import glob
from sklearn.metrics import confusion_matrix
import pandas as pd

####
####Define Recognizer
####
global filelist_path
filelist_path='./filelist/'
global extension
extension='.txt'
global filenames
filenames = ['c0','c1','c2','c3','c4','c5','c6','c7','c8','c9']
global filecount
filecount = [2489,2267,2317,2346,2326,2312,2325,2002,1911,2129]
global feature_size
feature_size=1024
global class_size
class_size=10
global model_name
model_name = 'triplet-loss'#'batch-triplet-loss'#'softmax'#
global accuracy_path
accuracy_path = './accuracy/'

def plot_confusion_matrix(df_confusion, title='Confusion matrix', cmap=plt.cm.gray_r):
    plt.matshow(df_confusion, cmap=cmap) # imshow
    #plt.title(title)
    plt.colorbar()
    tick_marks = np.arange(len(df_confusion.columns))
    plt.xticks(tick_marks, df_confusion.columns, rotation=45)
    plt.yticks(tick_marks, df_confusion.index)
    #plt.tight_layout()
    plt.ylabel(df_confusion.index.name)
    plt.xlabel(df_confusion.columns.name)

class Recognizer(caffe.Net):
    """
    Recognizer extends Net for image class prediction
    by scaling, center cropping, or oversampling.

    Parameters
    ----------
    image_dims : dimensions to scale input for cropping/sampling.
        Default is to scale to net input size for whole-image crop.
    mean, input_scale, raw_scale, channel_swap: params for
        preprocessing options.
    """
    def __init__(self, model_file, pretrained_file, mean_file=None,
		 image_dims=(227, 227),
		 raw_scale=255,
                 channel_swap=(2,1,0),
  		 input_scale=None):
	#set GPU mode
	caffe.set_mode_gpu()
	#init net
        caffe.Net.__init__(self, model_file, pretrained_file, caffe.TEST)
        # configure pre-processing
        in_ = self.inputs[0]
        self.transformer = caffe.io.Transformer(
            {in_: self.blobs[in_].data.shape})
        self.transformer.set_transpose(in_, (2, 0, 1))
        if mean_file is not None:
	    proto_data = open(mean_file, "rb").read()
	    mean_blob = caffe.io.caffe_pb2.BlobProto.FromString(proto_data)
	    mean = caffe.io.blobproto_to_array(mean_blob)[0]
            self.transformer.set_mean(in_, mean)
        if input_scale is not None:
            self.transformer.set_input_scale(in_, input_scale)
        if raw_scale is not None:
            self.transformer.set_raw_scale(in_, raw_scale)
        if channel_swap is not None:
            self.transformer.set_channel_swap(in_, channel_swap)

        self.crop_dims = np.array(self.blobs[in_].data.shape[2:])
        if not image_dims:
            image_dims = self.crop_dims
        self.image_dims = image_dims

    def alex_predict(self, oversample=True):
        """
        Predict classification probabilities of inputs.

        Parameters
        ----------
        inputs : iterable of (H x W x K) input ndarrays.
        oversample : boolean
            average predictions across center, corners, and mirrors
            when True (default). Center-only prediction when False.

        Returns
        -------
        predictions: (N x C) ndarray of class probabilities for N images and C
            classes.
        """
	#load files
	input_dir='/media/frank/Data/Database/ImageNet/Kaggle/train/c9'
        inputs =[caffe.io.load_image(im_f)
                 for im_f in glob.glob(input_dir + '/*.jpg')]
        # Scale to standardize input dimensions.
        input_ = np.zeros((len(inputs),
                           self.image_dims[0],
                           self.image_dims[1],
                           inputs[0].shape[2]),
                          dtype=np.float32)
        for ix, in_ in enumerate(inputs):
            input_[ix] = caffe.io.resize_image(in_, self.image_dims)

        if oversample:
            # Generate center, corner, and mirrored crops.
            input_ = caffe.io.oversample(input_, self.crop_dims)
        else:
            # Take center crop.
            center = np.array(self.image_dims) / 2.0
            crop = np.tile(center, (1, 2))[0] + np.concatenate([
                -self.crop_dims / 2.0,
                self.crop_dims / 2.0
            ])
            crop = crop.astype(int)
            input_ = input_[:, crop[0]:crop[2], crop[1]:crop[3], :]

        # Classify
        caffe_in = np.zeros(np.array(input_.shape)[[0, 3, 1, 2]],
                            dtype=np.float32)
        for ix, in_ in enumerate(input_):
            caffe_in[ix] = self.transformer.preprocess(self.inputs[0], in_)
        out = self.forward_all(**{self.inputs[0]: caffe_in})
        predictions = out[self.outputs[0]]

        # For oversampling, average predictions across crops.
        if oversample:
            predictions = predictions.reshape((len(predictions) / 10, 10, -1))
            predictions = predictions.mean(1)

        return predictions

    def read_imagelist(self,filelist):
	fid=open(filelist)
	lines=fid.readlines()
	test_num=len(lines)
	fid.close()
	X=np.empty((test_num,3,self.image_dims[0],self.image_dims[1]))
	i =0
	for line in lines:
	  word=line.split('\n')
	  filename=word[0]
	  im1=skimage.io.imread(filename,as_grey=False)
	  image =skimage.transform.resize(im1,(self.image_dims[0], self.image_dims[1]))*255
	  if image.ndim<3:
	    print 'gray:'+filename
	    X[i,0,:,:]=image[:,:]
	    X[i,1,:,:]=image[:,:]
	    X[i,2,:,:]=image[:,:]
	  else:
	    X[i,0,:,:]=image[:,:,2]
	    X[i,1,:,:]=image[:,:,0]
	    X[i,2,:,:]=image[:,:,1]
	  i=i+1
	return X

    def read_labels(labelfile):
	fin=open(labelfile)
	lines=fin.readlines()
	labels=np.empty((len(lines),))
	k=0;
	for line in lines:
	  labels[k]=int(line)
	  k=k+1;
	fin.close()
	return labels

    def draw_roc_curve(fpr,tpr,title='cosine',save_name='roc_lfw'):
	plt.figure()
	plt.plot(fpr, tpr)
	plt.plot([0, 1], [0, 1], 'k--')
	plt.xlim([0.0, 1.0])
	plt.ylim([0.0, 1.0])
	plt.xlabel('False Positive Rate')
	plt.ylabel('True Positive Rate')
	plt.title('Receiver operating characteristic using: '+title)
	plt.legend(loc="lower right")
	plt.show()
	plt.savefig(save_name+'.png')

    #predict_test_alexnet
    def test_alex(self):
	
	class_index = 0
	image_index = 0
	total_count = 0.0
	accept_sum = 0
	actual = []
	predict = []

	for filename in filenames:
	    #query-feature
	    X=self.read_imagelist(filelist_path + filename + extension)
	    test_num=np.shape(X)[0]
	    out = self.forward_all(data=X)
	    predicts=out[self.outputs[0]]
	    predicts=np.reshape(predicts,(test_num,10))
	    confusion_array = np.zeros((class_size), dtype = np.int)
	    for i in range(test_num):
		actual.append(class_index)
		for j in range(class_size):    
		   if np.max(predicts[i]) == predicts[i][j]:
			confusion_array[j] += 1	
			predict.append(j)
		image_index += 1
	    #print(confusion_array)
	    total_count += test_num
	    accept_sum += confusion_array[class_index]
	    class_index += 1
	
	print 'total:%d' % (round(total_count))
	print 'accept:%d' % (accept_sum)
	print 'reject:%d' % (round(total_count) - accept_sum)
	print 'accuray:%.4f' % (accept_sum / total_count)

	#conf_mat = confusion_matrix(actual,predict)
	#print(conf_mat)
	#actual = np.array(actual)
	#predict = np.array(predict)
	#y_actual = pd.Series(actual, name='Actual')
	#y_predict = pd.Series(predict, name='Predicted')
	#df_confusion = pd.crosstab(y_actual,y_predict, rownames=['Actual'], colnames=['Predicted'], margins=True)
	#print(df_confusion)
	#plot_confusion_matrix(df_confusion)
	return (accept_sum / total_count)

    #process a text file
    def evaluate(self,metric='cosine'):
	#sample-feature
	X=self.read_imagelist(filelist_sample)
	sample_num=np.shape(X)[0]
	out = self.forward_all(data=X)
	feature1=np.float64(out['deepid'])
	feature1=np.reshape(feature1,(sample_num,feature_size))
	#np.savetxt('feature1.txt', feature1, delimiter=',')
	
	class_index = 0
	image_index = 0
	total_count = 0.0
	accept_sum = 0
	actual = []
	predict = []

	for filename in filenames:
	    #query-feature
	    X=self.read_imagelist(filelist_path + filename + extension)
	    test_num=np.shape(X)[0]
	    out = self.forward_all(data=X)
	    feature2=np.float64(out['deepid'])
	    feature2=np.reshape(feature2,(test_num,feature_size))
	    #np.savetxt('feature2.txt', feature2, delimiter=',')
	    #mt=pw.pairwise_distances(feature2, feature1, metric=metric)
	    mt=pw.cosine_similarity(feature2, feature1)
	    false=0
	    for i in range(test_num):
		actual.append(class_index)
		for j in range(sample_num):
		   if np.max(mt[i]) == mt[i][j]:
			confusion_array[j] += 1	
			predict.append(j)
		image_index += 1

	    total_count += test_num
	    accept_sum += confusion_array[class_index]
	    class_index += 1
	
	print 'total:%d' % (round(total_count))
	print 'accept:%d' % (accept_sum)
	print 'reject:%d' % (round(total_count) - accept_sum)
	print 'accuray:%.4f' % (accept_sum / total_count)

	#conf_mat = confusion_matrix(actual,predict)
	#print(conf_mat)
	actual = np.array(actual)
	predict = np.array(predict)
	y_actual = pd.Series(actual, name='Actual')
	y_predict = pd.Series(predict, name='Predicted')
	df_confusion = pd.crosstab(y_actual,y_predict, rownames=['Actual'], colnames=['Predicted'], margins=True)
	print(df_confusion)
	plot_confusion_matrix(df_confusion)
	return (accept_sum / total_count)
    
    #process a text file
    def evaluate2(self,metric='cosine'):
	feature1=np.fromfile('./features/' + model_name +'-features.dat',dtype=np.float64)
	feature1=np.reshape(feature1,(class_size,feature_size))
	#np.savetxt('feature1.txt', feature1, delimiter=',')
	
	class_index = 0
	image_index = 0
	total_count = 0.0
	accept_sum = 0
	actual = []
	predict = []
	for filename in filenames:
	    #query-feature
	    X=self.read_imagelist(filelist_path + filename + extension)
	    test_num=np.shape(X)[0]
	    out = self.forward_all(data=X)
	    feature2=np.float64(out['deepid'])
	    feature2=np.reshape(feature2,(test_num,feature_size))
	    #np.savetxt('feature2.txt', feature2, delimiter=',')
	    #mt=pw.pairwise_distances(feature2, feature1, metric=metric)
	    mt=pw.cosine_similarity(feature2, feature1)
	    false=0
	    for i in range(test_num):
		actual.append(class_index)
		for j in range(class_size):
		   if np.max(mt[i]) == mt[i][j]:
			confusion_array[j] += 1	
			predict.append(j)
		image_index += 1

	    total_count += test_num
	    accept_sum += confusion_array[class_index]
	    class_index += 1
	
	print 'total:%d' % (round(total_count))
	print 'accept:%d' % (accept_sum)
	print 'reject:%d' % (round(total_count) - accept_sum)
	print 'accuray:%.4f' % (accept_sum / total_count)

	#conf_mat = confusion_matrix(actual,predict)
	#print(conf_mat)
	#actual = np.array(actual)
	#predict = np.array(predict)
	#y_actual = pd.Series(actual, name='Actual')
	#y_predict = pd.Series(predict, name='Predicted')
	#df_confusion = pd.crosstab(y_actual,y_predict, rownames=['Actual'], colnames=['Predicted'], margins=True)
	#print(df_confusion)
	#plot_confusion_matrix(df_confusion)
	return (accept_sum / total_count)
	
    #process a text file
    def evaluate3(self,metric='cosine'):
	feature1=np.fromfile('./features/' + model_name +'-features.dat',dtype=np.float64)
	feature1=np.reshape(feature1,(class_size,feature_size))
	
	class_index = 0
	image_index = 0
	total_count = 0.0
	accept_sum = 0
	top5_accept_sum = 0
	actual = []
	predict = []
	for filename in filenames:
	    #query-feature
	    #X=self.read_imagelist(filelist_path + filename + extension)
	    test_num = filecount[class_index]#np.shape(X)[0]
	    feature2=np.fromfile('./features/' + model_name +'-features-c' + str(class_index) + '.dat',dtype=np.float64)
	    feature2=np.reshape(feature2,(test_num,feature_size))
	    mt=pw.cosine_similarity(feature2, feature1)
	    top5_accept = 0
	    confusion_array = np.zeros((class_size), dtype = np.int)
	    for i in range(test_num):
		actual.append(class_index)
		sort_array = np.zeros((class_size), dtype = np.float64)
		for j in range(class_size):
		   sort_array[j] = mt[i][j]
		   if np.max(mt[i]) == mt[i][j]:
			confusion_array[j] += 1	
			predict.append(j)
			break
		#print(sort_array)
		sort_array.sort()
		#print(sort_array)
		for j in range((class_size - 5),class_size):
		   if sort_array[j] == mt[i][class_index]:
			top5_accept += 1
			break
		image_index += 1

	    total_count += test_num
	    accept_sum += confusion_array[class_index]
	    top5_accept_sum += top5_accept
	    class_index += 1
	
	print 'total:%d' % (round(total_count))
	print 'accept:%d' % (accept_sum)
	print 'reject:%d' % (round(total_count) - accept_sum)
	print 'top 1 accuray:%.4f' % (accept_sum / total_count)
	print 'top 5 accuray:%.4f' % (top5_accept_sum / total_count)
	#conf_mat = confusion_matrix(actual,predict)
	#print(conf_mat)
	actual = np.array(actual)
	predict = np.array(predict)
	y_actual = pd.Series(actual, name='Actual')
	y_predict = pd.Series(predict, name='Predicted')
	df_confusion = pd.crosstab(y_actual,y_predict, rownames=['Actual'], colnames=['Predicted'], margins=True)
	print(df_confusion)
	#plot_confusion_matrix(df_confusion)

	result = []
	result.append(accept_sum / total_count)
	result.append(top5_accept_sum / total_count)
	return result

    #save features
    def saveFeature(self):
        averages=np.zeros((class_size,feature_size),dtype=np.float64)
	i=0
	for filename in filenames:
	    #query-feature
	    X=self.read_imagelist(filelist_path + filename + extension)
	    test_num=np.shape(X)[0]
	    out = self.forward_all(data=X)
	    feature2=np.float64(out['deepid'])
	    feature2.tofile('./features/' + model_name + '-features-c' + str(i) + '.dat')
	    feature2=np.reshape(feature2,(test_num,feature_size))
	    average=np.zeros((feature_size),dtype=np.float64)
	    for j in range(test_num):
	    	average[:] = average[:] + feature2[j,:]
	    average[:]=average[:]/test_num
	    averages[i,:]=average[:]
	    i=i+1
	averages.tofile('./features/' + model_name + '-features.dat')

    #process an image file
    def getFeature2(self,imgfile):
        img=skimage.io.imread(imgfile,as_grey=False)
        resized =skimage.transform.resize(img,(self.image_dims[0], self.image_dims[1]))*255
	X=np.empty((1,3,self.image_dims[0],self.image_dims[1]))
    	X[0,0,:,:]=resized[:,:,2]
    	X[0,1,:,:]=resized[:,:,0]
	X[0,2,:,:]=resized[:,:,1]
	test_num=np.shape(X)[0]
        out = self.forward_all(data=X)
	#extract feature
	feature = np.float64(out['deepid'])
	feature=np.reshape(feature,(test_num,feature_size))
        return feature

    def compare_pic(self,feature1,feature2):
	predicts=pw.pairwise_distances(feature2, feature1,'cosine')
	#predicts=pw.cosine_similarity(feature1, feature2)
	return  predicts

    def compare_pic2(self,path1,path2):
	feature1 = self.getFeature2(path1)
	feature2 = self.getFeature2(path2)
 	predicts = self.compare_pic(feature1,feature2)
	return predicts

    def classify(self,path):
	feature1 = self.getFeature2(path)
	fid=open('./filelist/sample.txt')
	lines=fid.readlines()
	test_num=len(lines)
	fid.close()
	i =0
	msg='out:'
	for line in lines:
	  word=line.split('\n')
	  filename=word[0]
	  feature2 = self.getFeature2(filename)
	  predicts = self.compare_pic(feature1,feature2)
	  tmp='(%d,%f)'%(i,predicts)
	  msg=msg+tmp	  
	  i=i+1
	print msg

if __name__ == '__main__':
    
    step = 2
    top1_accuracy = []
    top5_accuracy = []
    for i in range(30,30 + 1):
	iteration = str(i * step)
    	tripletnet= Recognizer('./models/deploy.prototxt',
    			    	   './data/models/triplet/alexnet_triplet_iter_' + iteration + '.caffemodel',
    	                	   './data/models/softmax/mean.binaryproto')
    	#alexnet= Recognizer('/home/frank/triplet-master/data/models/softmax/deploy.prototxt',
    	#	       	    '/home/frank/digits/digits/jobs/20170429-175608-c101/snapshot_iter_' + iteration + '.caffemodel',
        #            	    '/home/frank/triplet-master/data/models/softmax/mean.binaryproto')
	
    	##ALEXNET TEST
    	#start = time.time()
    	#model_accuracy = alexnet.test_alex()

    	##TRIPLET TEST
    	#tripletnet.saveFeature()
    	model_accuracy = tripletnet.evaluate3()
	top1_accuracy.append(model_accuracy[0])
	top5_accuracy.append(model_accuracy[1])
	#with open(accuracy_path + model_name + '-top1-' +iteration + '.txt', 'w') as file:
	#     file.write(str(model_accuracy[0]))
	#with open(accuracy_path + model_name + '-top5-' +iteration + '.txt', 'w') as file:
	#     file.write(str(model_accuracy[1]))
																																																										
    top1_accuracy = np.array(top1_accuracy)
    #top1_accuracy.tofile(accuracy_path + model_name + '-top1.dat')
    #np.savetxt(accuracy_path + model_name + '-top1.out',top1_accuracy,delimiter=',')
    top5_accuracy = np.array(top5_accuracy)
    #top5_accuracy.tofile(top5_accuracy + model_name + '-top5.dat')
    #np.savetxt(accuracy_path + model_name + '-top5.out',top5_accuracy,delimiter=',')
    #print(accuracy)
    	

