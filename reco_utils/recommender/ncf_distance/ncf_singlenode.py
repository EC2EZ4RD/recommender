# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

import os
import numpy as np
import pandas as pd
import tensorflow as tf
from time import time
import logging

logger = logging.getLogger(__name__)

MODEL_CHECKPOINT = "model.ckpt"


def point_segment_distance(segment_p_0, segment_p_1, segment_q_0, segment_q_1):
    """Shortest distance between a point and a segment.
    Args:
        point (np.ndarray): Point
        segment (list): Segment
    Returns:
        bool: Shortest distance.
    """
    u = (segment_p_1 - segment_p_0)
    v = (segment_q_1 - segment_q_0)
    w = (segment_p_0 - segment_q_0)

    a = tf.reduce_sum(tf.multiply(u, u), 1, keep_dims=True)
    b = tf.reduce_sum(tf.multiply(u, v), 1, keep_dims=True)
    c = tf.reduce_sum(tf.multiply(v, v), 1, keep_dims=True)
    d = tf.reduce_sum(tf.multiply(u, w), 1, keep_dims=True)
    e = tf.reduce_sum(tf.multiply(v, w), 1, keep_dims=True)
    D = a * c - b * b

    condition_1 = tf.less(D, 1e-7)
    condition_2 = tf.less((b * e - c * d), 0.0)
    condition_3 = tf.greater((b * e - c * d), D)
    sN = tf.where(condition_1, tf.zeros_like(D),
                  tf.where(condition_2, tf.zeros_like(D), tf.where(condition_3, D, (b * e - c * d))))
    sD = tf.where(condition_1, tf.ones_like(D), D)
    tN = tf.where(condition_1, e, tf.where(condition_2, e, tf.where(condition_3, e + b, (a * e - b * d))))
    tD = tf.where(condition_1, c, tf.where(condition_2, c, tf.where(condition_3, c, D)))

    # if D < 1e-7:
    #     sN = 0.0
    #     sD = 1.0
    #     tN = e
    #     tD = c
    # else:
    #     sN = (b * e - c * d)
    #     tN = (a * e - b * d)
    #     if sN < 0.0:
    #         sN = 0.0
    #         tN = e
    #         tD = c
    #     elif sN > sD:
    #         sN = sD
    #         tN = e + b
    #         tD = c

    condition_4 = tf.less(tN, 0)
    condition_5 = tf.less(-d, 0)
    condition_6 = tf.greater(-d, a)
    condition_7 = tf.less(-d + b, 0)
    condition_8 = tf.greater(-d + b, a)
    condition_9 = tf.greater(tN, tD)

    tN = tf.where(condition_4, tf.zeros_like(D), tf.where(condition_9, tD, tN))
    sN = tf.where(condition_4, tf.where(condition_5, tf.zeros_like(D), tf.where(condition_6, sD, -d)),
                  tf.where(condition_9, tf.where(condition_7, tf.zeros_like(D), tf.where(condition_8, sD, -d + b)),
                           sN))
    sD = tf.where(condition_4, tf.where(condition_5, sD, tf.where(condition_6, sD, a)),
                  tf.where(condition_9, tf.where(condition_7, sD, tf.where(condition_8, sD, a)),
                           sD))

    # if tN < 0.0:
    #     tN = 0.0
    #     if -d < 0.0:
    #         sN = 0.0
    #     elif -d > a:
    #         sN = sD
    #     else:
    #         sN = -d
    #         sD = a
    # elif tN > tD:
    #     tN = tD
    #     if (-d + b) < 0.0:
    #         sN = 0
    #     elif ((-d + b) > a):
    #         sN = sD
    #     else:
    #         sN = (-d + b)
    #         sD = a

    sc = tf.where(tf.less(tf.abs(sN), 1e-7), tf.zeros_like(D), sN / sD)
    tc = tf.where(tf.less(tf.abs(tN), 1e-7), tf.zeros_like(D), tN / tD)

    # sc = 0.0 if abs(sN) < 1e-7 else sN / sD
    # tc = 0.0 if abs(tN) < 1e-7 else tN / tD

    dP = w + (sc * u) - (tc * v)

    return tf.reshape(tf.norm(dP, axis=-1), (-1, 1))


class NCF:
    """Neural Collaborative Filtering (NCF) implementation
    
    Note:

        He, Xiangnan, Lizi Liao, Hanwang Zhang, Liqiang Nie, Xia Hu, and Tat-Seng Chua. "Neural collaborative filtering." 
        In Proceedings of the 26th International Conference on World Wide Web, pp. 173-182. International World Wide Web 
        Conferences Steering Committee, 2017. Link: https://www.comp.nus.edu.sg/~xiangnan/papers/ncf.pdf
    """

    def __init__(
            self,
            n_users,
            n_items,
            model_type="NeuMF",
            n_factors=8,
            layer_sizes=[16, 8, 4],
            n_epochs=50,
            batch_size=64,
            learning_rate=5e-3,
            verbose=1,
            seed=None,
    ):
        """Constructor
        
        Args:
            n_users (int): Number of users in the dataset.
            n_items (int): Number of items in the dataset.
            model_type (str): Model type.
            n_factors (int): Dimension of latent space.
            layer_sizes (list): Number of layers for MLP.
            n_epochs (int): Number of epochs for training.
            batch_size (int): Batch size.
            learning_rate (float): Learning rate.
            verbose (int): Whether to show the training output or not.
            seed (int): Seed.
        
        """

        # seed
        tf.compat.v1.set_random_seed(seed)
        np.random.seed(seed)
        self.seed = seed

        self.n_users = n_users
        self.n_items = n_items
        self.model_type = model_type.lower()
        self.n_factors = n_factors
        self.layer_sizes = layer_sizes
        self.n_epochs = n_epochs
        self.verbose = verbose
        self.batch_size = batch_size
        self.learning_rate = learning_rate

        # check model type
        model_options = ["gmf", "mlp", "neumf"]
        if self.model_type not in model_options:
            raise ValueError(
                "Wrong model type, please select one of this list: {}".format(
                    model_options
                )
            )

        # ncf layer input size
        self.ncf_layer_size = n_factors + layer_sizes[-1]
        # create ncf model
        self._create_model()
        # set GPU use with demand growth
        gpu_options = tf.compat.v1.GPUOptions(allow_growth=True)
        # set TF Session
        self.sess = tf.compat.v1.Session(config=tf.compat.v1.ConfigProto(gpu_options=gpu_options))
        # parameters initialization
        self.sess.run(tf.compat.v1.global_variables_initializer())

    def _create_model(self, ):
        # reset graph
        tf.compat.v1.reset_default_graph()

        with tf.compat.v1.variable_scope("input_data", reuse=tf.compat.v1.AUTO_REUSE):

            # input: index of users, items and ground truth
            self.user_input = tf.compat.v1.placeholder(tf.int32, shape=[None, 1])
            self.item_input = tf.compat.v1.placeholder(tf.int32, shape=[None, 1])
            self.labels = tf.compat.v1.placeholder(tf.float32, shape=[None, 1])

        with tf.compat.v1.variable_scope("embedding", reuse=tf.compat.v1.AUTO_REUSE):

            # set embedding table
            self.embedding_gmf_P = tf.Variable(
                tf.random.truncated_normal(
                    shape=[self.n_users, self.n_factors],
                    mean=0.0,
                    stddev=0.01,
                    seed=self.seed,
                ),
                name="embedding_gmf_P",
                dtype=tf.float32,
            )

            self.embedding_gmf_Q = tf.Variable(
                tf.random.truncated_normal(
                    shape=[self.n_items, self.n_factors],
                    mean=0.0,
                    stddev=0.01,
                    seed=self.seed,
                ),
                name="embedding_gmf_Q",
                dtype=tf.float32,
            )

            # set embedding table
            self.embedding_mlp_P = tf.Variable(
                tf.random.truncated_normal(
                    shape=[self.n_users, int(self.layer_sizes[0] / 2)],
                    mean=0.0,
                    stddev=0.01,
                    seed=self.seed,
                ),
                name="embedding_mlp_P",
                dtype=tf.float32,
            )

            self.embedding_mlp_Q = tf.Variable(
                tf.random.truncated_normal(
                    shape=[self.n_items, int(self.layer_sizes[0] / 2)],
                    mean=0.0,
                    stddev=0.01,
                    seed=self.seed,
                ),
                name="embedding_mlp_Q",
                dtype=tf.float32,
            )

        with tf.compat.v1.variable_scope("gmf", reuse=tf.compat.v1.AUTO_REUSE):

            # get user embedding p and item embedding q
            self.gmf_p_internal = tf.reduce_sum(
                tf.nn.embedding_lookup(self.embedding_gmf_P, self.user_input), 1
            )
            self.gmf_q_internal = tf.reduce_sum(
                tf.nn.embedding_lookup(self.embedding_gmf_Q, self.item_input), 1
            )

            self.gmf_p_variational = tf.contrib.layers.fully_connected(
                self.gmf_p_internal,
                num_outputs=2 * self.n_factors,
                weights_initializer=tf.contrib.layers.xavier_initializer(
                    seed=self.seed
                ),
            )

            self.gmf_q_variational = tf.contrib.layers.fully_connected(
                self.gmf_q_internal,
                num_outputs=2 * self.n_factors,
                weights_initializer=tf.contrib.layers.xavier_initializer(
                    seed=self.seed
                ),
            )

            gmf_p_0 = self.gmf_p_variational[:, :self.n_factors]
            gmf_p_1 = self.gmf_p_variational[:, self.n_factors:]

            gmf_q_0 = self.gmf_q_variational[:, :self.n_factors]
            gmf_q_1 = self.gmf_q_variational[:, self.n_factors:]

            # 0.5*(gmf_p_mean+gmf_q_mean)
            # 0.25 * (tf.square(gmf_p_stddev) + tf.square(gmf_q_stddev))

            from scipy.spatial.distance import directed_hausdorff
            self.gmf_vector = point_segment_distance(gmf_p_0, gmf_p_1, gmf_q_0, gmf_q_1)
            # self.gmf_vector = 0.5*tf.math.log(2*np.pi*0.25*(tf.square(gmf_p_stddev)+tf.square(gmf_q_stddev)))-0.25*tf.math.log(2*np.pi*tf.square(gmf_p_stddev))-0.25*tf.math.log(2*np.pi*tf.square(gmf_q_stddev))

            # get gmf vector
            # self.gmf_vector = self.gmf_p * self.gmf_q

        with tf.compat.v1.variable_scope("mlp", reuse=tf.compat.v1.AUTO_REUSE):

            # get user embedding p and item embedding q
            self.mlp_p = tf.reduce_sum(
                tf.nn.embedding_lookup(self.embedding_mlp_P, self.user_input), 1
            )
            self.mlp_q = tf.reduce_sum(
                tf.nn.embedding_lookup(self.embedding_mlp_Q, self.item_input), 1
            )

            # concatenate user and item vector
            output = tf.concat([self.mlp_p, self.mlp_q], 1)

            # MLP Layers
            for layer_size in self.layer_sizes[1:]:
                output = tf.contrib.layers.fully_connected(
                    output,
                    num_outputs=layer_size,
                    activation_fn=tf.nn.relu,
                    weights_initializer=tf.contrib.layers.xavier_initializer(
                        seed=self.seed
                    ),
                )
            self.mlp_vector = output

            # self.output = tf.sigmoid(tf.reduce_sum(self.mlp_vector, axis=1, keepdims=True))

        with tf.compat.v1.variable_scope("ncf", reuse=tf.compat.v1.AUTO_REUSE):

            if self.model_type == "gmf":
                # GMF only
                output = tf.contrib.layers.fully_connected(
                    self.gmf_vector,
                    num_outputs=1,
                    activation_fn=None,
                    biases_initializer=None,
                    weights_initializer=tf.contrib.layers.xavier_initializer(
                        seed=self.seed
                    ),
                )
                self.output = tf.sigmoid(output)

            elif self.model_type == "mlp":
                # MLP only
                output = tf.contrib.layers.fully_connected(
                    self.mlp_vector,
                    num_outputs=1,
                    activation_fn=None,
                    biases_initializer=None,
                    weights_initializer=tf.contrib.layers.xavier_initializer(
                        seed=self.seed
                    ),
                )
                self.output = tf.sigmoid(output)

            elif self.model_type == "neumf":
                # concatenate GMF and MLP vector
                self.ncf_vector = tf.concat([self.gmf_vector, self.mlp_vector], 1)
                # get predicted rating score
                output = tf.contrib.layers.fully_connected(
                    self.ncf_vector,
                    num_outputs=1,
                    activation_fn=None,
                    biases_initializer=None,
                    weights_initializer=tf.contrib.layers.xavier_initializer(
                        seed=self.seed
                    ),
                )
                self.output = tf.sigmoid(output)

        with tf.compat.v1.variable_scope("loss", reuse=tf.compat.v1.AUTO_REUSE):

            # set loss function
            self.loss = tf.compat.v1.losses.log_loss(self.labels, self.output)

        with tf.compat.v1.variable_scope("optimizer", reuse=tf.compat.v1.AUTO_REUSE):

            # set optimizer
            self.optimizer = tf.compat.v1.train.AdamOptimizer(
                learning_rate=self.learning_rate
            ).minimize(self.loss)

    def save(self, dir_name):
        """Save model parameters in `dir_name`
        
        Args:
            dir_name (str): directory name, which should be a folder name instead of file name
                we will create a new directory if not existing.
        """
        # save trained model
        if not os.path.exists(dir_name):
            os.makedirs(dir_name)
        saver = tf.compat.v1.train.Saver()
        saver.save(self.sess, os.path.join(dir_name, MODEL_CHECKPOINT))

    def load(self, gmf_dir=None, mlp_dir=None, neumf_dir=None, alpha=0.5):
        """Load model parameters for further use.
        
        GMF model --> load parameters in `gmf_dir`
        
        MLP model --> load parameters in `mlp_dir`
        
        NeuMF model --> load parameters in `neumf_dir` or in `gmf_dir` and `mlp_dir`
        
        Args:
            gmf_dir (str): Directory name for GMF model.
            mlp_dir (str): Directory name for MLP model.
            neumf_dir (str): Directory name for neumf model.
            alpha (float): the concatenation hyper-parameter for gmf and mlp output layer.
        
        Returns:
            obj: Load parameters in this model.
        """

        # load pre-trained model
        if self.model_type == "gmf" and gmf_dir is not None:
            saver = tf.compat.v1.train.Saver()
            saver.restore(self.sess, os.path.join(gmf_dir, MODEL_CHECKPOINT))

        elif self.model_type == "mlp" and mlp_dir is not None:
            saver = tf.compat.v1.train.Saver()
            saver.restore(self.sess, os.path.join(mlp_dir, MODEL_CHECKPOINT))

        elif self.model_type == "neumf" and neumf_dir is not None:
            saver = tf.compat.v1.train.Saver()
            saver.restore(self.sess, os.path.join(neumf_dir, MODEL_CHECKPOINT))

        elif self.model_type == "neumf" and gmf_dir is not None and mlp_dir is not None:
            # load neumf using gmf and mlp
            self._load_neumf(gmf_dir, mlp_dir, alpha)

        else:
            raise NotImplementedError

    def _load_neumf(self, gmf_dir, mlp_dir, alpha):
        """Load gmf and mlp model parameters for further use in NeuMF.
            NeuMF model --> load parameters in `gmf_dir` and `mlp_dir`
        """
        # load gmf part
        variables = tf.compat.v1.global_variables()
        # get variables with 'gmf'
        var_flow_restore = [
            val for val in variables if "gmf" in val.name and "ncf" not in val.name
        ]
        # load 'gmf' variable
        saver = tf.compat.v1.train.Saver(var_flow_restore)
        # restore
        saver.restore(self.sess, os.path.join(gmf_dir, MODEL_CHECKPOINT))

        # load mlp part
        variables = tf.compat.v1.global_variables()
        # get variables with 'gmf'
        var_flow_restore = [
            val for val in variables if "mlp" in val.name and "ncf" not in val.name
        ]
        # load 'gmf' variable
        saver = tf.compat.v1.train.Saver(var_flow_restore)
        # restore
        saver.restore(self.sess, os.path.join(mlp_dir, MODEL_CHECKPOINT))

        # concat pretrain h_from_gmf and h_from_mlp
        vars_list = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope="ncf")

        assert len(vars_list) == 1
        ncf_fc = vars_list[0]

        # get weight from gmf and mlp
        gmf_fc = tf.contrib.framework.load_variable(gmf_dir, ncf_fc.name)
        mlp_fc = tf.contrib.framework.load_variable(mlp_dir, ncf_fc.name)

        # load fc layer by tf.concat
        assign_op = tf.assign(
            ncf_fc, tf.concat([alpha * gmf_fc, (1 - alpha) * mlp_fc], axis=0)
        )
        self.sess.run(assign_op)

    def fit(self, data):
        """Fit model with training data
            
        Args: 
            data (NCFDataset): initilized Dataset in ./dataset.py
        """

        # get user and item mapping dict
        self.user2id = data.user2id
        self.item2id = data.item2id
        self.id2user = data.id2user
        self.id2item = data.id2item

        # loop for n_epochs
        for epoch_count in range(1, self.n_epochs + 1):

            # negative sampling for training
            train_begin = time()
            data.negative_sampling()

            # initialize
            train_loss = []

            # calculate loss and update NCF parameters
            for user_input, item_input, labels in data.train_loader(self.batch_size):
                user_input = np.array([self.user2id[x] for x in user_input])
                item_input = np.array([self.item2id[x] for x in item_input])
                labels = np.array(labels)

                feed_dict = {
                    self.user_input: user_input[..., None],
                    self.item_input: item_input[..., None],
                    self.labels: labels[..., None],
                }

                # get loss and execute optimization
                loss, _ = self.sess.run([self.loss, self.optimizer], feed_dict)
                train_loss.append(loss)
            train_time = time() - train_begin

            # output every self.verbose
            if self.verbose and epoch_count % self.verbose == 0:
                logger.info(
                    "Epoch %d [%.2fs]: train_loss = %.6f "
                    % (epoch_count, train_time, sum(train_loss) / len(train_loss))
                )

    def predict(self, user_input, item_input, is_list=False):
        """Predict function of this trained model
            
        Args:
            user_input (list or element of list): userID or userID list 
            item_input (list or element of list): itemID or itemID list
            is_list (bool): if true, the input is list type
                noting that list-wise type prediction is faster than element-wise's.
        
        Returns:
            list or float: list of predicted rating or predicted rating score. 
        """

        if is_list:
            output = self._predict(user_input, item_input)
            return list(output.reshape(-1))

        else:
            output = self._predict(np.array([user_input]), np.array([item_input]))
            return float(output.reshape(-1)[0])

    def _predict(self, user_input, item_input):

        # index converting
        user_input = np.array([self.user2id[x] for x in user_input])
        item_input = np.array([self.item2id[x] for x in item_input])

        # get feed dict
        feed_dict = {
            self.user_input: user_input[..., None],
            self.item_input: item_input[..., None],
        }

        # calculate predicted score
        return self.sess.run(self.output, feed_dict)
