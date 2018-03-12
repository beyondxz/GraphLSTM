import rnn_cell_impl as rci
import networkx as nx
import tensorflow as tf
import numpy as np
from tensorflow.python.ops import rnn_cell_impl as orig_rci
# TODO what is tensorflow.python.user_ops for?
import graph as rci_graph
import unittest

# test graph: 20 nodes
_kickoff_hand = [("t0", "wrist"), ("i0", "wrist"), ("m0", "wrist"), ("r0", "wrist"), ("p0", "wrist"), ("i0", "m0"),
                 ("m0", "r0"), ("r0", "p0"), ("t0", "t1"), ("t1", "t2"), ("i0", "i1"), ("i1", "i2"), ("i2", "i3"),
                 ("m0", "m1"), ("m1", "m2"), ("m2", "m3"), ("r0", "r1"), ("r1", "r2"), ("r2", "r3"), ("p0", "p1"),
                 ("p1", "p2"), ("p2", "p3")]

_CELL = rci._CELL
_INDEX = rci._INDEX
_CONFIDENCE = rci._CONFIDENCE


# cell that always returns fixed value on call()
class DummyFixedCell(orig_rci.RNNCell):

    def __init__(self, return_value=None, state_is_tuple=True):
        super(DummyFixedCell, self).__init__()
        self._returnValue = return_value
        self._state_is_tuple = state_is_tuple

    @property
    def state_size(self):
        return None

    @property
    def output_size(self):
        return None

    def call(self, inputs, state, neighbour_states):
        return self._returnValue


class DummyFixedTfCell(orig_rci.RNNCell):
    def __init__(self, num_units=1, memory_state=((2.,),), hidden_state=((3.,),), state_is_tuple=True):
        if not state_is_tuple:
            raise NotImplementedError("DummyFixedTfCell is only defined for state_is_tuple=True")
        super(DummyFixedTfCell, self).__init__()
        self._num_units = num_units
        self._m = tf.constant(memory_state)
        self._h = tf.constant(hidden_state)

    @property
    def state_size(self):
        return self._num_units, self._num_units

    @property
    def output_size(self):
        return self._num_units

    # get neighbour_states from net without embedding it in the state itself
    def __call__(self, inputs, state, neighbour_states, *args, **kwargs):
        self._neighbour_states = neighbour_states
        return super(DummyFixedTfCell, self).__call__(inputs, state, *args, **kwargs)

    def call(self, inputs, state):
        scope = tf.get_variable_scope()
        with tf.variable_scope(scope) as outer_scope:
            return self._h, (self._m, self._h)


# cell that always returns inputs, state, and neighbour_states on call()
class DummyReturnCell(orig_rci.RNNCell):

    def __init__(self, state_is_tuple=True):
        super(DummyReturnCell, self).__init__()
        self._state_is_tuple = state_is_tuple

    @property
    def state_size(self):
        return None

    @property
    def output_size(self):
        return None

    def call(self, inputs, state, neighbour_states):
        return (inputs, state, neighbour_states), (neighbour_states, state, inputs)


# cell that always returns (-inputs) and state or sum of neighbour states
class DummyReturnTfCell(orig_rci.RNNCell):
    def __init__(self, num_units, state_is_tuple=True, return_sum_of_neighbour_states=False,
                 add_one_to_state_per_timestep=False):
        if not state_is_tuple:
            raise NotImplementedError("DummyFixedTfCell is only defined for state_is_tuple=True")
        super(DummyReturnTfCell, self).__init__()
        self._num_units = num_units
        self._return_sum_of_neighbour_states = return_sum_of_neighbour_states
        self._add_one = add_one_to_state_per_timestep

    @property
    def state_size(self):
        return self._num_units, self._num_units

    @property
    def output_size(self):
        return self._num_units

    # get neighbour_states from net without embedding them in the state itself
    def __call__(self, inputs, state, neighbour_states, *args, **kwargs):
        self._neighbour_states = neighbour_states
        return super(DummyReturnTfCell, self).__call__(inputs, state, *args, **kwargs)

    def call(self, inputs, state):
        if self._return_sum_of_neighbour_states:
            state = tf.add_n([m for m, h in self._neighbour_states]), tf.add_n([h for m, h in self._neighbour_states])
        elif self._add_one:
            state = tuple(x+1 for x in state)
        return -inputs, state


class TestGraphLSTMNet(tf.test.TestCase):

    def setUp(self):
        self.longMessage = True
        self.G = nx.Graph(_kickoff_hand)
        
    def test_create_nxgraph(self):
        # template for creating graph a-b-c
        v_template = [("c", "b"), ("b", "a")]
        # the method to be tested
        cg = rci.GraphLSTMNet.create_nxgraph

        # invalid graphs
        self.assertRaises(ValueError, cg, None)
        self.assertRaises(TypeError, cg, "Teststring")
        self.assertRaises(TypeError, cg, 5)
        self.assertRaises(ValueError, cg, [])
        self.assertRaises(TypeError, cg, ["1", "2", "2"])

        # valid graph, but invalid keywords
        # num_units < 1
        self.assertRaises(ValueError, cg, v_template, 0)
        # num_units must be int
        self.assertRaises(TypeError, cg, v_template, 1.0)
        self.assertRaises(TypeError, cg, v_template, "one")
        # confidence_dict must be a dict
        self.assertRaises(TypeError, cg, v_template, 1, confidence_dict=False)
        # confidence_dict may not contain invalid node names
        self.assertRaises(KeyError, cg, v_template, 1, confidence_dict={"z": 1})
        # confidence_dict may not contain invalid confidence values
        self.assertRaises(ValueError, cg, v_template, 1, confidence_dict={"a": "A"})

        # valid graph, valid keywords
        v_graph = cg(v_template, 6, confidence_dict={"a": .3, "c": -500})
        self.assertIs(v_graph.number_of_nodes(), 3)
        self.assertIs(v_graph.number_of_edges(), 2)
        self.assertEqual(sorted(v_graph.nodes()), sorted(['a', 'b', 'c']))
        for n in ['a', 'b', 'c']:
            self.assertEqual(v_graph.node[n].keys(), {_CONFIDENCE, _INDEX, _CELL})
        self.assertEqual(v_graph.node['a'][_CONFIDENCE], .3)
        self.assertEqual(v_graph.node['b'][_CONFIDENCE], 0)
        self.assertEqual(v_graph.node['c'][_CONFIDENCE], -500)
        self.assertEqual(v_graph.node['a'][_INDEX], 0)
        self.assertEqual(v_graph.node['b'][_INDEX], 1)
        self.assertEqual(v_graph.node['c'][_INDEX], 2)
        for n in ['a', 'b', 'c']:
            self.assertIsInstance(v_graph.node[n][_CELL], rci.GraphLSTMCell)
            self.assertEqual(v_graph.node[n][_CELL].output_size, 6)

        # **kwargs
        # invalid keyword
        self.assertRaises(TypeError, cg, v_template, 6, invalid_keyword=99)
        # valid keywords
        v_graph = cg(v_template, 6, forget_bias=99, activation="xyz")
        for n in ['a', 'b', 'c']:
            self.assertEqual(v_graph.node[n][_CELL]._forget_bias, 99)
            self.assertEqual(v_graph.node[n][_CELL]._activation, "xyz")
            
    def test_init(self):
        # GraphLSTMNet should complain when initiated with something else than a nx.Graph
        # like an int ...
        self.assertRaises(TypeError, rci.GraphLSTMNet, 3)
        # ... None ...
        self.assertRaises(ValueError, rci.GraphLSTMNet, None)
        # ... or nothing at all
        self.assertRaises(TypeError, rci.GraphLSTMNet)

    def test__cell(self):
        test_node = "test_node"
        gnet = rci.GraphLSTMNet(self.G, num_units=1, name="unittest_setup_gnet")
        gnet._nxgraph.add_node(test_node)
        # GraphLSTMNet._cell should complain when asked for non-existent node ...
        self.assertRaises(KeyError, gnet._cell, "_")
        # ... or existing node without a cell
        self.assertRaises(KeyError, gnet._cell, test_node)
        # Check if return values for existing cells are right
        gnet._nxgraph.node[test_node][_CELL] = 123
        self.assertEqual(gnet._cell(test_node), 123)
        glcell = rci.GraphLSTMCell
        b = glcell(1)
        gnet._nxgraph.node["t0"][_CELL] = b
        self.assertIs(gnet._cell("t0"), b)
        b = glcell(1)
        self.assertIsNot(gnet._cell("t0"), b)
        self.assertIsInstance(gnet._cell("wrist"), rci.GraphLSTMCell)

    @unittest.skip("'inputs' is a tensor when called by tensorflow. Threw no errors as of 2018-02-27,"
                   "maybe implement with tensor-input later")
    def test_call_uninodal_notf(self):
        cell_input, cell_state_m, cell_state_h, cell_cur_output, cell_new_state = objects(5)

        unet, cname = self.get_uninodal_graphlstmnet()

        # test correct returning of cell return value
        unet._nxgraph.node[cname][_CELL] = DummyFixedCell((cell_cur_output, cell_new_state)).call
        net_output = unet.call(([cell_input]), ((cell_state_m, cell_state_h),))
        expected = ((cell_cur_output,), (cell_new_state,))
        self.assertEqual(net_output, expected, msg="GraphLSTNet.call() did not return expected objects. "
                                                   "There is probably an error in GraphLSTMNet AFTER calling the cell.")

        # test correct delivering of parameters to cell
        unet._nxgraph.node[cname][_CELL] = DummyReturnCell().call
        net_output = unet.call(([cell_input]), ((cell_state_m, cell_state_h),))
        expected = (((cell_input, (cell_state_m, cell_state_h), tuple()),),
                    ((tuple(), (cell_state_m, cell_state_h), cell_input),))
        self.assertEqual(net_output, expected, msg="GraphLSTNet.call() did not deliver expected objects to cell. "
                                                   "There is probably an error in GraphLSTMNet BEFORE calling the cell.")

        # check proper index handling: uninodal GraphLSTM should complain about indices > 0
        unet._nxgraph.node[cname][_INDEX] = 1
        self.assertRaises(IndexError, unet.call, ([cell_input]), ((cell_state_m, cell_state_h),))

    def test_call_uninodal_tf(self):
        # set up net
        net, cell_name = self.get_uninodal_graphlstmnet()

        # init cells
        # constant return value 1-dim
        constant_cell_1 = DummyFixedTfCell()
        # m = [[1,2],[3,4],[5,6]], h=[[7,8],[9,10],[11,12]] (batch size 3, state size 2)
        constant_cell_2 = DummyFixedTfCell(num_units=2, memory_state=((1., 2.), (3., 4.), (5., 6.)),
                                           hidden_state=((7., 8.), (9., 10.), (11., 12.)))
        # simple return cell, 4 units
        return_cell_1 = DummyReturnTfCell(4)
        # 3 units, increase state by 1 each time step
        return_cell_2 = DummyReturnTfCell(3, add_one_to_state_per_timestep=True)

        # dimensions: batch_size, max_time, [cell dimensions] e.g. for
        #   GraphLSTMCell: input_size
        #   GraphLSTMNet: cell_count, input_size

        # fixed cell 1: 1 unit, input values arbitrary

        # input size 4: [1 1 1 4]
        input_data_xc1 = tf.placeholder(tf.float32, [None, None, 1, 4])
        feed_dict_xc1a = {input_data_xc1: [[[[6, 5, 4, 3]]]]}
        # input_size is ignored by constant cell
        cc1a_expected_result = [[[3]]], ([[2]], [[3]])

        # 1000 timesteps: [1 1000 1 1]
        input_data_cc1b = tf.placeholder(tf.float32, [None, None, 1, 1])
        feed_dict_cc1b = {input_data_cc1b: np.random.rand(1, 1000, 1, 1)}
        # timesteps are managed by dynamic_rnn
        cc1b_expected_result = [[[3]] * 1000], ([[2]], [[3]])

        # batch size 3: [3 1 1 1]
        feed_dict_cc1c = {input_data_cc1b: [[[[4]]], [[[17]]], [[[-9]]]]}
        # batch_size is ignored by constant cell
        cc1c_expected_result = [[[3]]], ([[2]], [[3]])

        # fixed cell 2: 3 units, input values arbitrary

        # batch size 3, 4 timesteps, input size 5
        input_data_cc2 = tf.placeholder(tf.float32, [None, None, 1, 5])
        feed_dict_cc2 = {
            input_data_cc2: np.random.rand(3, 4, 1, 5)}
        cc2_expected_result = [[[7, 8]] * 4, [[9, 10]] * 4, [[11, 12]] * 4], \
                              ([[1, 2], [3, 4], [5, 6]], [[7, 8], [9, 10], [11, 12]])

        # return cell 1: 4 units

        # input size 4: [1 1 1 4] (unmodified state -> zero-state)
        rc1a_expected_result = [[[-6, -5, -4, -3]]], ([[0, 0, 0, 0]], [[0, 0, 0, 0]])

        # 1000 timesteps, input size 4: [1 1000 1 4]
        rc1b_input_values = np.random.rand(1, 1000, 1, 4)
        feed_dict_rc1b = {input_data_xc1: rc1b_input_values}
        rc1b_expected_result = -np.squeeze(rc1b_input_values, 2), ([[0, 0, 0, 0]], [[0, 0, 0, 0]])

        # batch size 3, input size 4: [3 1 1 4]
        rc1c_input_values = np.random.rand(3, 1, 1, 4)
        feed_dict_rc1c = {input_data_xc1: rc1c_input_values}
        rc1c_expected_result = -np.squeeze(rc1c_input_values, 2), ([[0, 0, 0, 0]]*3, [[0, 0, 0, 0]]*3)

        # return cell 2: 3 units, add_one_to_state_per_timestep True

        # batch size 2, 5 timesteps, input size 3:
        input_data_rc2 = tf.placeholder(tf.float32, [None, None, 1, 3])
        rc2_input_values = np.random.rand(2, 5, 1, 3)
        feed_dict_rc2 = {input_data_rc2: rc2_input_values}
        rc2_expected_result = -np.squeeze(rc2_input_values, 2), ([[5, 5, 5]]*2, [[5, 5, 5]]*2)

        with self.test_session():
            tf.global_variables_initializer().run()

            # return value of GraphLSTMNet: graph_output, new_states
            # return value of DummyFixedTfCell: output, (state, output)
            # return value of DummyReturnTfCell: input, state
            # return value of dynamic_rnn: output [batch_size, max_time, cell.output_size], final_state

            # if tests containing DummyFixedTfCells fail, this might mean there are problems in GraphLSTMNet
            # AFTER calling the cell
            msg = "Calling GraphLSTNet with dummy cells did not return expected values. " \
                  "This could mean there is an error in GraphLSTMNet AFTER calling the cell."

            # inject first fixed-cell into graph
            net._nxgraph.node[cell_name][_CELL] = constant_cell_1

            cc1a_returned_tensors = tf.nn.dynamic_rnn(net, input_data_xc1, dtype=tf.float32)

            cc1a_actual_result = cc1a_returned_tensors[0].eval(feed_dict=feed_dict_xc1a), \
                (cc1a_returned_tensors[1][0][0].eval(feed_dict=feed_dict_xc1a),
                 cc1a_returned_tensors[1][0][1].eval(feed_dict=feed_dict_xc1a))
            np.testing.assert_equal(cc1a_actual_result, cc1a_expected_result, err_msg=msg)

            cc1bc_returned_tensors = tf.nn.dynamic_rnn(net, input_data_cc1b, dtype=tf.float32)

            cc1b_actual_result = cc1bc_returned_tensors[0].eval(feed_dict=feed_dict_cc1b), \
                (cc1bc_returned_tensors[1][0][0].eval(feed_dict=feed_dict_cc1b),
                 cc1bc_returned_tensors[1][0][1].eval(feed_dict=feed_dict_cc1b))
            np.testing.assert_equal(cc1b_actual_result, cc1b_expected_result, err_msg=msg)

            cc1c_actual_result = cc1bc_returned_tensors[0].eval(feed_dict=feed_dict_cc1c), \
                (cc1bc_returned_tensors[1][0][0].eval(feed_dict=feed_dict_cc1c),
                 cc1bc_returned_tensors[1][0][1].eval(feed_dict=feed_dict_cc1c))
            np.testing.assert_equal(cc1c_actual_result, cc1c_expected_result, err_msg=msg)

            # inject second fixed-cell into graph
            net._nxgraph.node[cell_name][_CELL] = constant_cell_2

            cc2_returned_tensor = tf.nn.dynamic_rnn(net, input_data_cc2, dtype=tf.float32)

            cc2_actual_result = cc2_returned_tensor[0].eval(feed_dict=feed_dict_cc2), \
                                (cc2_returned_tensor[1][0][0].eval(feed_dict=feed_dict_cc2),
                                 cc2_returned_tensor[1][0][1].eval(feed_dict=feed_dict_cc2))
            np.testing.assert_equal(cc2_actual_result, cc2_expected_result, err_msg=msg)

            # if tests containing DummyReturnTfCells fail, while those containing DummyFixedTfCells
            # do not, this might mean there are problems in GraphLSTMNet BEFORE calling the cell
            msg = "Calling GraphLSTNet with return cells did not return expected values. " \
                  "This could mean there is an error in GraphLSTMNet BEFORE calling the cell."

            # inject first return-cell into graph
            net._nxgraph.node[cell_name][_CELL] = return_cell_1

            rc1_returned_tensor = tf.nn.dynamic_rnn(net, input_data_xc1, dtype=tf.float32)
            rc1a_actual_result = rc1_returned_tensor[0].eval(feed_dict=feed_dict_xc1a), \
                                (rc1_returned_tensor[1][0][0].eval(feed_dict=feed_dict_xc1a),
                                 rc1_returned_tensor[1][0][1].eval(feed_dict=feed_dict_xc1a))
            np.testing.assert_equal(rc1a_actual_result, rc1a_expected_result, err_msg=msg)

            rc1b_actual_result = rc1_returned_tensor[0].eval(feed_dict=feed_dict_rc1b), \
                                 (rc1_returned_tensor[1][0][0].eval(feed_dict=feed_dict_rc1b),
                                  rc1_returned_tensor[1][0][1].eval(feed_dict=feed_dict_rc1b))
            np.testing.assert_allclose(rc1b_actual_result[0], rc1b_expected_result[0], err_msg=msg)
            np.testing.assert_equal(rc1b_actual_result[1:], rc1b_expected_result[1:], err_msg=msg)

            rc1c_actual_result = rc1_returned_tensor[0].eval(feed_dict=feed_dict_rc1c), \
                                 (rc1_returned_tensor[1][0][0].eval(feed_dict=feed_dict_rc1c),
                                  rc1_returned_tensor[1][0][1].eval(feed_dict=feed_dict_rc1c))
            np.testing.assert_allclose(rc1c_actual_result[0], rc1c_expected_result[0], err_msg=msg)
            np.testing.assert_equal(rc1c_actual_result[1:], rc1c_expected_result[1:], err_msg=msg)

            # inject second return-cell into graph
            net._nxgraph.node[cell_name][_CELL] = return_cell_2

            rc2_returned_tensor = tf.nn.dynamic_rnn(net, input_data_rc2, dtype=tf.float32)
            rc2_actual_result = rc2_returned_tensor[0].eval(feed_dict=feed_dict_rc2), \
                                 (rc2_returned_tensor[1][0][0].eval(feed_dict=feed_dict_rc2),
                                  rc2_returned_tensor[1][0][1].eval(feed_dict=feed_dict_rc2))
            np.testing.assert_allclose(rc2_actual_result[0], rc2_expected_result[0], err_msg=msg)
            np.testing.assert_equal(rc2_actual_result[1:], rc2_expected_result[1:], err_msg=msg)

    @staticmethod
    def get_uninodal_graphlstmnet(cell_name="node0", confidence=0):
        graph = nx.Graph()
        graph.add_node(cell_name)
        nxgraph = rci.GraphLSTMNet.create_nxgraph(graph, 1, confidence_dict={cell_name: confidence})
        net = rci.GraphLSTMNet(nxgraph)
        return net, cell_name


class TestGraphLSTMLinear(tf.test.TestCase):

    def setUp(self):
        self.longMessage = True
        self.func = rci._graphlstm_linear

        self.x = tf.constant([[1., 2.], [3., 4.]])
        self.y = tf.constant([[5., 6.], [7., 8.]])
        self.z = tf.constant([[0., 1.], [2., 3.], [4., 5.]])
        self.custom_initializer_1 = tf.constant_initializer([[0, -1], [2, 1]])

    def test_errors(self):
        self.assertRaisesRegex(ValueError, "args", self.func, ['_'], [], 1, True)
        self.assertRaisesRegex(ValueError, "weight_names", self.func, [], ['_'], 1, True)
        self.assertRaisesRegex(ValueError, "True.*one element longer", self.func, ['1', '2'], ['1', '2'], 1, True)
        self.assertRaisesRegex(ValueError, "False.*same length", self.func, ['1', '2'], ['1'], 1, False)
        self.assertRaisesRegex(LookupError, "`reuse_weights`", self.func, ['1', '2'], ['3', '4'], 1, False,
                               reuse_weights=['3'])

    def test_calc(self):
        w1 = "weight_name_1"
        w2 = "weight_name_2"
        w3 = "weight_name_3"
        w4 = "weight_name_4"
        b1 = "bias_name_1"
        b2 = "bias_name_2"
        b3 = "bias_name_3"
        n = "non_existing_name"

        # x * w1 (1 1,1 1)
        glxw1 = self.func([w1], self.x, 2, False, weight_initializer=tf.ones_initializer)
        glxw1_expected_result = [[3, 3], [7, 7]]
        # existing variable should throw error when fetched without reuse
        self.assertRaisesRegex(ValueError, "already exists", self.func, w1, self.x, 2, False)
        # new variable should throw error when fetched with reuse
        self.assertRaisesRegex(ValueError, "does not exist", self.func, n, self.x, 2, False, reuse_weights=n)
        # x * w1 + y * w2 (0 -1,2 1) + b1 (0 0)
        glxw1yw2b1 = self.func([w1, w2, b1], [self.x, self.y], 2, True, weight_initializer=self.custom_initializer_1,
                               reuse_weights=[w1])
        glxw1yw2b1_expected_result = [[15, 4], [23, 8]]
        # y * w1 + x * w2 + b2 (1 1)
        glyw1xw2b2 = self.func([w1, w2, b2], [self.y, self.x], 2, True, bias_initializer=tf.ones_initializer,
                               reuse_weights=[w1, w2])
        glyw1xw2b2_expected_result = [[16, 13], [24, 17]]
        # non-square matrices
        # x * w3 (1 1)
        glxw3 = self.func(w3, self.x, 1, False, weight_initializer=tf.ones_initializer)
        glxw3_expected_result = [[3], [7]]
        # z * w4 (1 1 1 1,1 1 1 1,1 1 1 1) + b3 (1 1 1 1)
        glzw4b3 = self.func([w4, b3], self.z, 4, True, weight_initializer=tf.ones_initializer,
                            bias_initializer=tf.ones_initializer)
        glzw4b3_expected_result = [[2, 2, 2, 2], [6, 6, 6, 6], [10, 10, 10, 10]]

        with self.test_session():
            tf.global_variables_initializer().run()

            np.testing.assert_equal(glxw1.eval(), glxw1_expected_result)
            np.testing.assert_equal(glxw1yw2b1.eval(), glxw1yw2b1_expected_result)
            np.testing.assert_equal(glyw1xw2b2.eval(), glyw1xw2b2_expected_result)
            np.testing.assert_equal(glxw3.eval(), glxw3_expected_result)
            np.testing.assert_equal(glzw4b3.eval(), glzw4b3_expected_result)


# print node information for graph or GraphLSTMNet g
def print_node(name, g):
    if isinstance(g, rci.GraphLSTMNet):
        print("Node information for GraphLSTMNet %s:" % str(g))
        g = g._nxgraph
    else:
        print("Node information for graph %s:" % str(g))
    print("graph[\"%s\"]: %s" % (name, str(g[name])))
    print("graph.node[\"%s\"]: %s" % (name, str(g.node[name])))


# return tuple of n objects
def objects(n):
    r = []
    for _ in range(n):
        r.append(object())
    return tuple(r)


def dirty_tests():
    G = nx.Graph()
    G.add_node("a", cell="heyho")
    g = rci.GraphLSTMNet.create_nxgraph(G, ignore_cell_type=True)
    rci.GraphLSTMNet.is_valid_nxgraph(g, ignore_cell_type=True)


def main():
    dirty_tests()
    with tf.variable_scope("unittest"):
        unittest.main()


main()
