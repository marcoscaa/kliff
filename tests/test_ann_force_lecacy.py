from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
import numpy as np
import tensorflow as tf
from openkim_fit.dataset import DataSet
from openkim_fit.descriptor import Descriptor
import openkim_fit.ann as ann

#
# See test_ann_force.py for the latest version.
#

# set a global random seed
tf.set_random_seed(1)


# read config and reference data
tset = DataSet()
tset.read('./training_set/graphene_bilayer_1x1.xyz')
#tset.read('./training_set/training_set_mos2_small_config_4/mos2_2x2_a3.0.xyz')
configs = tset.get_configs()
conf = configs[0]

# create Descriptor
cutfunc = 'cos'
cutvalue = {'C-C':5.}
#cutvalue = {'Mo-Mo':5., 'Mo-S':5., 'S-S':5.}
desc_params = {'g1': None,
                 'g2': [{'eta':0.1, 'Rs':0.2},
                        {'eta':0.3, 'Rs':0.4}],
                 'g3': [{'kappa':0.1},
                        {'kappa':0.2},
                        {'kappa':0.3}],
                 'g4': [{'zeta':0.1, 'lambda':0.2, 'eta':0.01},
                        {'zeta':0.3, 'lambda':0.4, 'eta':0.02}],
                 'g5': [{'zeta':0.11, 'lambda':0.22, 'eta':0.011},
                        {'zeta':0.33, 'lambda':0.44, 'eta':0.022}]
                }

desc = Descriptor(desc_params, cutfunc, cutvalue,  cutvalue_samelayer=cutvalue, debug=True)

# create params (we need to share params among different config, so create first)
num_desc = desc.get_num_descriptors()
weights,biases = ann.parameters(num_desc, [20, 10, 1], dtype=tf.float64)

# build nn
in_layer, coords = ann.input_layer(configs[0], desc, dtype=tf.float64)
dense1 = ann.nn_layer(in_layer, weights[0], biases[0], 'hidden1',act=tf.nn.tanh)
dense2 = ann.nn_layer(dense1, weights[1], biases[1], 'hidden2', act=tf.nn.tanh)
output = ann.output_layer(dense2, weights[2], biases[2], 'outlayer')

# energy and forces
energy = tf.reduce_sum(output)
forces = tf.gradients(output, coords)[0]  # tf.gradients return a LIST of tensors

with tf.Session() as sess:

  # init global vars
  init_op = tf.global_variables_initializer()
  sess.run(init_op)

  out = sess.run(energy)
  print('energy:', out)

  out = sess.run(forces)
  print('forces:')
  for i,f in enumerate(out):
    print('{:13.5e}'.format(f), end='')
    if i%3==2:
      print()

  # output results to a KIM model
  w,b = sess.run([weights, biases])
  ann.write_kim_ann(desc, w, b, tf.nn.tanh, dtype=tf.float64)

