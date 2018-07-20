import graph_lstm as glstm
import region_ensemble.model as re
from helpers import *

import tensorflow as tf
import keras.backend as K
from keras.optimizers import Adam
from keras.utils import plot_model
from keras.callbacks import ModelCheckpoint, TensorBoard

import numpy as np
import seaborn as sns
import zipfile

from tqdm import tqdm
import os


# set plot style
# sns.set_style("whitegrid")


# dataset path declarations

prefix = "train-04"
checkpoint_dir = r"/home/matthias-k/GraphLSTM_data/%s" % prefix

dataset_root = r"/home/matthias-k/datasets/hands2017/data/hand2017_nor_img_new"
train_and_validate_list = ["nor_%08d.pkl" % i for i in range(1000, 957001, 1000)] + ["nor_00957032.pkl"]

train_list, validate_list = train_validate_split(train_and_validate_list)

testset_root = r"/data2/datasets/hands2017/data/hand2017_test_0914"
test_list = ["%08d.pkl" % i for i in range(10000, 290001, 10000)] + ["00295510.pkl"]

# number of timesteps to be simulated (each step, the same data is fed)
graphlstm_timesteps = 2
learning_rate = 1e-3

model_name = "regen41_graphlstm1bf1t%i_rescon_adamlr%f" % (graphlstm_timesteps, learning_rate)

checkpoint_dir += r"/%s" % model_name
tensorboard_dir = checkpoint_dir + r"/tensorboard"


# # PREPARE SESSION

# set Keras session
config = tf.ConfigProto(log_device_placement=True, allow_soft_placement=True)
config.gpu_options.allow_growth = True
sess = tf.Session(config=config)
K.set_session(sess)


# # BUILD MODEL

print("\n###   Building Model: %s   ###\n" % model_name)

print("Building RegionEnsemble network …")

# initialize region_ensemble_net
region_ensemble_net_pca = re.RegEnPCA(directory_prefix=prefix, use_precalculated_samples=True,
                                      dataset_root=dataset_root, train_list=train_and_validate_list)
region_ensemble_net = re.RegEnModel(directory_prefix=prefix)
region_ensemble_net.compile(optimizer=Adam(), loss=re.soft_loss)
region_ensemble_net.set_pca_bottleneck_weights(region_ensemble_net_pca)

regen_input_tensor = region_ensemble_net.input
regen_output_tensor = region_ensemble_net.output

# here the Region Ensemble network is done initialising


print("Building GraphLSTM network …")

# initialize Graph LSTM
# since a well-defined node order is necessary to correctly communicate with the Region Ensemble network,
# the graph must be created manually
nxgraph = glstm.GraphLSTMNet.create_nxgraph(HAND_GRAPH_HANDS2017,
                                            num_units=GLSTM_NUM_UNITS,
                                            index_dict=HAND_GRAPH_HANDS2017_INDEX_DICT)
graph_lstm_net = glstm.GraphLSTMNet(nxgraph, shared_weights=glstm.NEIGHBOUR_CONNECTIONS_SHARED)

# overall output dimensions: batch_size, number_of_nodes, output_size (unflatten RegEn output)
regen_output_tensor_reshaped = graph_lstm_net.reshape_input_for_dynamic_rnn(regen_output_tensor)

# normalize RegEn net output
regen_output_tensor_reshaped_normalized, undo_scaling = normalize_for_glstm(regen_output_tensor_reshaped)


# input dimensions of GraphLSTMNet: batch_size, max_time, number_of_nodes, input_size
graphlstm_input_tensor = graph_lstm_net.reshape_input_for_dynamic_rnn(regen_output_tensor_reshaped_normalized,
                                                                      timesteps=graphlstm_timesteps)

dynrnn_glstm_output_full, dynrnn_glstm_state = tf.nn.dynamic_rnn(graph_lstm_net,
                                                                 inputs=graphlstm_input_tensor,
                                                                 dtype=tf.float32)

# reorder dimensions to [batch_size, max_time, number_of_nodes, output_size]
glstm_output_full = graph_lstm_net.transpose_output_from_cells_first_to_batch_first(dynrnn_glstm_output_full)
# extract last timestep from output
glstm_output = tf.unstack(glstm_output_full, axis=1)[-1]
# denormalize GraphLSTM output
glstm_output_rescaled = undo_scaling(glstm_output)

# here the Graph LSTM network is done initializing

# employ residual connections around Graph LSTM
residual_merge = tf.add(regen_output_tensor_reshaped, glstm_output_rescaled)

print("Finished building model.\n")


# # TRAIN

print("Preparing training …")

max_epoch = 100
start_epoch = 1

input_shape = region_ensemble_net.input_shape  # = [None, *re.Const.MODEL_IMAGE_SHAPE]
input_tensor = regen_input_tensor

output_shape = [None, len(graph_lstm_net.output_size), graph_lstm_net.output_size[0]]
output_tensor = residual_merge

groundtruth_tensor = tf.placeholder(tf.float32, shape=output_shape)

loss = re.soft_loss(groundtruth_tensor, output_tensor)
train_step = tf.train.AdamOptimizer(learning_rate=learning_rate).minimize(loss)

if not os.path.exists(checkpoint_dir):
    os.makedirs(checkpoint_dir)
    print("Created new checkpoint directory `%s`." % checkpoint_dir)
saver = tf.train.Saver(keep_checkpoint_every_n_hours=0.1, filename=checkpoint_dir)

# gather tensors for tensorboard
tf.summary.scalar('loss', loss)
tf.summary.histogram('Graph LSTM output', glstm_output)
tf.summary.histogram('Region Ensemble net output', regen_output_tensor_reshaped)
tf.summary.histogram('Network output', output_tensor)
if not os.path.exists(tensorboard_dir):
    os.makedirs(tensorboard_dir)
    print("Created new tensorboard directory `%s`." % tensorboard_dir)
merged = tf.summary.merge_all()
training_summary_writer = tf.summary.FileWriter(tensorboard_dir, sess.graph)

# gather tensors needed for resuming after loading
tf.add_to_collection(COLLECTION, input_tensor)
tf.add_to_collection(COLLECTION, output_tensor)
tf.add_to_collection(COLLECTION, groundtruth_tensor)
tf.add_to_collection(COLLECTION, train_step)
tf.add_to_collection(COLLECTION, loss)
tf.add_to_collection(COLLECTION, merged)

t = TQDMHelper()

print("Initializing variables …")

sess.run(tf.global_variables_initializer())

with sess.as_default():
    print("Saving model meta graph …")
    saver.export_meta_graph(filename=checkpoint_dir + "/%s.meta" % model_name)

    print("Starting training.")

    global_step = 0
    for epoch in range(start_epoch, max_epoch + 1):
        t.start()
        training_sample_generator = re.pair_batch_generator_one_epoch(dataset_root, train_list,
                                                                      re.Const.TRAIN_BATCH_SIZE,
                                                                      shuffle=True, progress_desc="Epoch %i" % epoch,
                                                                      leave=False, epoch=epoch - 1)  # todo augmented=True?

        for batch in training_sample_generator:
            X, Y = batch
            actual_batch_size = X.shape[0]
            X = X.reshape([actual_batch_size, *input_shape[1:]])
            Y = Y.reshape([actual_batch_size, *output_shape[1:]])

            _, loss_value, summary = sess.run([train_step, loss, merged], feed_dict={input_tensor: X,
                                                                                     groundtruth_tensor: Y,
                                                                                     K.learning_phase(): 1})

            training_summary_writer.add_summary(summary, global_step=global_step)
            global_step += 1
            t.write("Current loss: %f" % loss_value)

            # todo: pass K.learning_phase(): 1 to feed_dict (for testing: 0)
        t.stop()
        print("Training loss after epoch %i: %f" % (epoch, loss_value))
        if epoch < 5 or epoch % 5 == 0:
            saver.save(sess, save_path=checkpoint_dir + "/%s" % model_name, global_step=epoch)

print("Training done, exiting.")
print("For validation, run: python validate.py %s %s" % (prefix, model_name))
exit(0)

train_batch_gen = re.pair_batch_generator(dataset_root, train_list, re.Const.TRAIN_BATCH_SIZE, shuffle=True, augmented=True)
validate_batch_gen = re.pair_batch_generator(dataset_root, validate_list, re.Const.VALIDATE_BATCH_SIZE)

# history = region_ensemble_net.fit_generator(
#     train_batch_gen,
#     steps_per_epoch=re.Const.NUM_TRAIN_BATCHES,
#     epochs=200,
#     initial_epoch=30,
#     callbacks=[
#         #         LearningRateScheduler(lr_schedule),
#         TensorBoard(log_dir="./%s" % region_ensemble_net.directory_prefix),
#         ModelCheckpoint(
#             filepath='./%s/region_ensemble_net.{epoch:02d}.hdf5' % region_ensemble_net.directory_prefix,
#         ),
#     ]
# )


# # Validate

validate_image_batch_gen = re.image_batch_generator(dataset_root, validate_list, re.Const.VALIDATE_BATCH_SIZE)

predictions = region_ensemble_net.predict_generator(
    validate_image_batch_gen,
    steps=re.Const.NUM_VALIDATE_BATCHES, verbose=1
)

# mean absolute error
validate_label_gen = re.sample_generator(dataset_root, "pose", validate_list)
validate_label = np.asarray(list(validate_label_gen))
print("average", np.abs(validate_label - predictions).mean())


# # Explore Validate

validate_model_image_gen = re.sample_generator(dataset_root, "image", train_list, resize_to_shape=re.Const.MODEL_IMAGE_SHAPE)
validate_src_image_gen = re.sample_generator(dataset_root, "image", train_list)
validate_label_gen = re.sample_generator(dataset_root, "pose", train_list)

validate_model_image = next(validate_model_image_gen)
validate_src_image = next(validate_src_image_gen)
validate_label = next(validate_label_gen)
validate_uvd = region_ensemble_net.predict(np.asarray([validate_model_image]))

re.plot_scatter3d(validate_src_image, pred=validate_uvd, true=validate_label)


# # Explore Test

test_model_image_gen = re.sample_generator(testset_root, "image", test_list, resize_to_shape=re.Const.MODEL_IMAGE_SHAPE)
test_src_image_gen = re.sample_generator(testset_root, "image", test_list)

test_model_image = next(test_model_image_gen)
test_src_image = next(test_src_image_gen)
test_uvd = region_ensemble_net.predict(np.asarray([test_model_image]))
re.plot_scatter2d(test_src_image, pred=test_uvd)
re.plot_scatter3d(test_src_image, pred=test_uvd)


# # Test All

test_image_batch_gen = re.image_batch_generator(testset_root, test_list, re.Const.TEST_BATCH_SIZE)
# test_params is already loaded

test_uvd = region_ensemble_net.predict_generator(
    test_image_batch_gen,
    steps=re.Const.NUM_TEST_BATCHES,
    max_queue_size=1000,
    use_multiprocessing=True,
    verbose=True,
)


# # Generate Zip

test_param_and_name_gen = re.param_and_name_generator(testset_root, 'tran_para_img', test_list)


pose_submit_gen = ("frame\\images\\{}\t{}".format(name, '\t'.join(map(str, xyz)))
                   for xyz, name
                   in re.test_xyz_and_name_gen(region_ensemble_net, testset_root=testset_root, test_list=test_list))

np.savetxt('./%s/result-newtest.txt' % prefix, np.asarray(list(pose_submit_gen)), delimiter='\n', fmt="%s")

with zipfile.ZipFile("./%s/result-newtest.zip" % prefix, 'w', zipfile.ZIP_DEFLATED) as zf:
    zf.write("./%s/result-newtest.txt" % prefix, "result.txt")

