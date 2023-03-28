import os
from pathlib import Path
from typing import List, Optional

import numpy as np
import torch
from loguru import logger

from kliff.descriptors.descriptor import Descriptor
from kliff.models.model_torch import ModelTorch


class NeuralNetwork(ModelTorch):
    """
    Neural Network model.

    A feed-forward neural network model.

    Args:
        descriptor:
            A descriptor that transforms atomic environment information to the
            fingerprints, which are used as the input for the neural network.
        seed: Global seed for random numbers.
    """

    def __init__(self, descriptor: Descriptor, seed=35):
        super(NeuralNetwork, self).__init__(descriptor, seed)

        self.layers = None

        logger.debug(f"`{self.__class__.__name__}` instantiated.")

    def add_layers(self, *layers):
        """
        Add layers to the sequential model.

        Args:
            layers: torch.nn layers ``torch.nn`` layers that are used to build a
                sequential model. Available ones including: torch.nn.Linear,
                torch.nn.Dropout, and torch.nn.Sigmoid among others.
                See https://pytorch.org/docs/stable/nn.html for a full list.
        """
        if self.layers is not None:
            raise NeuralNetworkError(
                "`add_layers()` called multiple times. It should be called only once."
            )
        else:
            self.layers = []

        for la in layers:
            self.layers.append(la)
            # set it as attr so that parameters are automatically registered
            setattr(self, "layer_{}".format(len(self.layers)), la)

        # check shape of first layer and last layer
        first = self.layers[0]
        if first.in_features != self.descriptor.get_size():
            raise NeuralNetworkError(
                f"Expect `in_features` of first layer ({first.in_features}) be equal "
                f"to descriptor size ({self.descriptor.get_size()})."
            )
        last = self.layers[-1]
        if last.out_features != 1:
            raise NeuralNetworkError("`out_features` of last layer should be 1.")

        # cast types
        self.type(self.dtype)

    def forward(self, x):
        """
        Forward pass through the neural network.

        Args:
            x: input descriptor to the neural network.

        Returns:
            The output of the neural network.
        """
        for layer in self.layers:
            x = layer(x)
        return x

    def write_kim_model(
        self,
        path: Optional[Path] = None,
        driver_name: str = "DUNN__MD_292677547454_000",
        dropout_ensemble_size: int = None,
    ):
        """
        Write out a model that is compatible with the KIM API.

        Args:
            path: Path to write the model. If `None`, defaults to
                `./NeuralNetwork_KLIFF__MO_000000111111_000`.
            driver_name: Name of the model driver.
            dropout_ensemble_size: Size of the dropout ensemble. Ignored if not
                fitting a dropout NN. Otherwise, defaults to 100 if `None`.
        """

        if path is None:
            model_name = "NeuralNetwork_KLIFF__MO_000000111111_000"
            path = Path.cwd().joinpath(model_name)
        else:
            path = Path(path).expanduser().resolve()
            model_name = str(path.name)
        if not path.exists():
            os.makedirs(path)

        desc_name = "descriptor.params"
        nn_name = "NN.params"
        dropout_name = "dropout_binary.params"

        param_files = [desc_name, nn_name, dropout_name]
        self._write_kim_cmakelists(
            path, model_name, driver_name, param_files, version="2.0.0"
        )
        self._write_kim_params(path, nn_name)
        self.descriptor.write_kim_params(path, desc_name)
        self._write_kim_dropout_binary(path, dropout_name, dropout_ensemble_size)

        logger.info(f"KLIFF trained model written to {path}.")

    def _write_kim_params(self, path, filename="NN.params"):
        weights, biases = self._get_weights_and_biases()
        activations = self._get_activations()
        drop_ratios = self._get_drop_ratios()

        # PyTorch uses x*W^T + b, so we need to transpose it.
        # see https://pytorch.org/docs/stable/nn.html#linear
        weights = [torch.t(w) for w in weights]

        with open(path.joinpath(filename), "w") as fout:
            # header
            fout.write("#" + "=" * 80 + "\n")
            fout.write(
                "# NN structure and parameters file generated by KLIFF\n"
                "# \n"
                '# Note that the NN assumes each row of the input "X" is an \n'
                "# observation, i.e. the layer is implemented as\n"
                "# Y = activation(XW + b).\n"
                '# You need to transpose your weight matrix if each column of "X" is \n'
                "# an observation.\n"
            )
            fout.write("#" + "=" * 80 + "\n\n")

            # number of layers
            num_layers = len(weights)
            fout.write(
                "{}    # number of layers (excluding input layer,including output "
                "layer)\n".format(num_layers)
            )

            # size of layers
            for b in biases:
                fout.write("{}  ".format(len(b)))
            fout.write("  # size of each layer (last must be 1)\n")

            # activation function
            activation = activations[0]
            fout.write("{}    # activation function\n".format(activation))

            # keep probability
            for i in drop_ratios:
                fout.write("{:.15g}  ".format(1.0 - i))
            fout.write("  # keep probability of input for each layer\n\n")

            # weights and biases
            for i, (w, b) in enumerate(zip(weights, biases)):
                # weight
                rows, cols = w.shape
                if i != num_layers - 1:
                    fout.write(
                        "# weight of hidden layer {},  shape({}, {})\n".format(
                            i + 1, rows, cols
                        )
                    )
                else:
                    fout.write(
                        "# weight of output layer, shape({}, {})\n".format(rows, cols)
                    )
                for line in w:
                    for item in line:
                        if self.dtype == torch.float64:
                            fout.write("{:23.15e}".format(item))
                        else:
                            fout.write("{:15.7e}".format(item))
                    fout.write("\n")

                # bias
                if i != num_layers - 1:
                    fout.write(
                        "# bias of hidden layer {}, shape({}, )\n".format(i + 1, cols)
                    )
                else:
                    fout.write("# bias of output layer, shape({}, )\n".format(cols))
                for item in b:
                    if self.dtype == torch.float64:
                        fout.write("{:23.15e}".format(item))
                    else:
                        fout.write("{:15.7e}".format(item))
                fout.write("\n\n")

    def _write_kim_dropout_binary(
        self, path, filename="dropout_binary.params", size=None
    ):
        drop_ratios = self._get_drop_ratios()
        keep_prob = [1.0 - i for i in drop_ratios]
        _, biases = self._get_weights_and_biases()
        num_units = [self.descriptor.get_size()] + [len(i) for i in biases]

        no_drop = np.all(np.asarray(drop_ratios) < 1e-10)
        if no_drop:
            size = 0
        else:
            if size is None:
                size = 100

        with open(path.joinpath(filename), "w") as fout:
            fout.write("#" + "=" * 80 + "\n")
            fout.write(
                "# Dropout binary parameters file generated by KLIFF.\n"
                "#\n"
                '# Note, "ensemble size = 0", means that no dropout needs to be\n'
                "# applied at all."
            )
            fout.write("#" + "=" * 80 + "\n\n")

            fout.write("{}  # ensemble size\n".format(size))
            for rep in range(size):
                fout.write("#" + "=" * 80 + "\n")
                fout.write("# instance {}\n".format(rep))
                for i in range(len(keep_prob)):
                    fout.write("# layer {}\n".format(i))
                    n = num_units[i]
                    k = keep_prob[i]
                    rnd = np.floor(np.random.uniform(k, k + 1, n))
                    rnd = np.asarray(rnd, dtype=np.intc)
                    for d in rnd:
                        d = 1 if d > 1 else d
                        d = 0 if d < 0 else d
                        fout.write("{} ".format(d))
                    fout.write("\n")

    @staticmethod
    def _write_kim_cmakelists(
        path: Path, model_name: str, driver_name: str, param_files: List[str], version
    ):
        with open(path.joinpath("CMakeLists.txt"), "w") as fout:
            fout.write("#\n")
            fout.write("# Contributors:\n")
            fout.write("#    KLIFF (https://kliff.readthedocs.io)\n")
            fout.write("#\n\n")
            fout.write("cmake_minimum_required(VERSION 3.4)\n\n")
            fout.write(
                "list(APPEND CMAKE_PREFIX_PATH $ENV{KIM_API_CMAKE_PREFIX_DIR})\n"
            )
            fout.write("find_package(KIM-API 2.0 REQUIRED CONFIG)\n")
            fout.write("if(NOT TARGET kim-api)\n")
            fout.write("  enable_testing()\n")
            fout.write(
                '  project("${KIM_API_PROJECT_NAME}" VERSION "${KIM_API_VERSION}"\n'
            )
            fout.write("    LANGUAGES CXX C Fortran)\n")
            fout.write("endif()\n\n")
            fout.write("add_kim_api_model_library(\n")
            fout.write(f'  NAME            "{model_name}"\n')
            fout.write(f'  DRIVER_NAME     "{driver_name}"\n')
            fout.write("  PARAMETER_FILES")
            for s in param_files:
                fout.write(' "{}"'.format(s))
            fout.write("\n")
            fout.write("  )\n")

    def _group_layers(
        self,
        param_layer=("Linear",),
        activ_layer=("Sigmoid", "Tanh", "ReLU", "ELU"),
        dropout_layer=("Dropout",),
    ):
        """
        Divide all the layers into groups.

        The first group is either an empty list or a `Dropout` layer for the input layer.
        The last group typically contains only a `Linear` layer.  For other groups, each
        group contains two, or three layers. `Linear` layer and an activation layer are
        mandatory, and a third `Dropout` layer is optional.

        Returns:
            groups: list of list of layers
        """

        groups = []
        new_group = []

        supported = param_layer + activ_layer + dropout_layer
        for i, layer in enumerate(self.layers):
            name = layer.__class__.__name__
            if name not in supported:
                raise NeuralNetworkError(
                    f"Layer `{name}` not supported by KIM model. Cannot proceed "
                    "to write."
                )

            if name in activ_layer:
                if i == 0:
                    raise NeuralNetworkError(f"First layer cannot be a `{name}` layer")
                if self.layers[i - 1].__class__.__name__ not in param_layer:
                    raise NeuralNetworkError(
                        f"Cannot convert to KIM model. a `{name}` layer must follow "
                        'a "Linear" layer.'
                    )

            if name[:7] in dropout_layer:
                if self.layers[i - 1].__class__.__name__ not in activ_layer:
                    raise NeuralNetworkError(
                        f"Cannot convert to KIM model. a `{name}` layer must follow "
                        "an activation layer."
                    )
            if name in param_layer:
                groups.append(new_group)
                new_group = []
            new_group.append(layer)
        groups.append(new_group)

        return groups, param_layer, activ_layer, dropout_layer

    def _get_weights_and_biases(self):
        """
        Get weights and biases of all layers that have weights and biases.
        """

        groups, supported, _, _ = self._group_layers()

        weights = []
        biases = []
        for i, g in enumerate(groups):
            if i != 0:
                layer = g[0]
                name = layer.__class__.__name__
                if name in supported:
                    weight = layer.weight
                    bias = layer.bias
                    weights.append(weight)
                    biases.append(bias)
        return weights, biases

    def _get_activations(self):
        """
        Get the activation of all layers.
        """

        groups, _, supported, _ = self._group_layers()

        activations = []
        for i, g in enumerate(groups):
            if i != 0 and i != (len(groups) - 1):
                layer = g[1]
                name = layer.__class__.__name__
                if name in supported:
                    activations.append(name.lower())
        return activations

    def _get_drop_ratios(self):
        """
        Get the dropout ratio of all layers.
        """

        groups, _, _, supported = self._group_layers()

        drop_ratios = []
        for i, g in enumerate(groups):
            if i == 0:
                if len(g) != 0:
                    layer = g[0]
                    name = layer.__class__.__name__
                    if name in supported:
                        drop_ratios.append(layer.p)
                else:
                    drop_ratios.append(0.0)
            elif i == len(groups) - 1:
                pass
            else:
                if len(g) == 3:
                    layer = g[2]
                    name = layer.__class__.__name__
                    if name in supported:
                        drop_ratios.append(layer.p)
                else:
                    drop_ratios.append(0.0)

        return drop_ratios


class NeuralNetworkError(Exception):
    def __init__(self, msg):
        super(NeuralNetworkError, self).__init__(msg)
        self.msg = msg
