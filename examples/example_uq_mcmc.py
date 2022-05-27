"""
.. _tut_mcmc:

MCMC sampling
=============

In this example, we demonstrate how to perform uncertainty quantification (UQ) using
parallel tempered MCMC (PTMCMC). We use a Stillinger-Weber (SW) potential for silicon
that is archived in OpenKIM_.

For simplicity, we only set the energy-scaling parameters, i.e., ``A`` and ``lambda`` as
the tunable parameters. Furthermore, these parameters are physically constrained to be
positive, thus we will work in log parameterization, i.e. ``log(A)`` and ``log(lambda)``.
These parameters will be calibrated to energies and forces of a small dataset,
consisting of 4 compressed and stretched configurations of diamond silicon structure.
"""


##########################################################################################
# To start, let's first install the SW model::
#
#    $ kim-api-collections-management install user SW_StillingerWeber_1985_Si__MO_405512056662_006
#
# .. seealso::
#    This installs the model and its driver into the ``User Collection``. See
#    :ref:`install_model` for more information about installing KIM models.


import numpy as np

from kliff.calculators import Calculator
from kliff.dataset import Dataset
from kliff.dataset.weight import MagnitudeInverseWeight
from kliff.loss import Loss
from kliff.models import KIMModel
from kliff.models.parameter_transform import LogParameterTransform
from kliff.utils import download_dataset

from kliff.uq import MCMC, mser, autocorr, rhat


#########################################################################################
# Before running MCMC, we need to define a loss function and train the model. More detail
# information about this step can be found in :ref:`tut_kim_sw` and
# :ref:`tut_params_transform`.

# Instantiate a transformation class to do the log parameter transform
param_names = ["A", "lambda"]
params_transform = LogParameterTransform(param_names)

# Create the model
model = KIMModel(
    model_name="SW_StillingerWeber_1985_Si__MO_405512056662_006",
    params_transform=params_transform,
)

# Set the tunable parameters and the initial guess
opt_params = {
    "A": [["default", -8.0, 8.0]],
    "lambda": [["default", -8.0, 8.0]],
}
model.set_opt_params(**opt_params)
model.echo_opt_params()

# Get the dataset and set the weights
dataset_path = download_dataset(dataset_name="Si_training_set_4_configs")
# Instantiate the weight class
weight = MagnitudeInverseWeight(
    weight_params={
        "energy_weight_params": [0.0, 0.1],
        "forces_weight_params": [0.0, 0.1],
    }
)
# Read the dataset and compute the weight
tset = Dataset(dataset_path, weight=weight)
configs = tset.get_configs()

# Create calculator
calc = Calculator(model)
ca = calc.create(configs)

# Instantiate the loss function
residual_data = {"normalize_by_natoms": False}
loss = Loss(calc, residual_data=residual_data)

# Train the model
loss.minimize(method="L-BFGS-B", options={"disp": True})
model.echo_opt_params()


#########################################################################################
# To perform MCMC simulation, we use :class:`~kliff.uq.MCMC`.This class interfaces with
# ptemcee_ Python package to run PTMCMC, which utilizes the affine invariance property
# of MCMC sampling. We simulate MCMC sampling at several different temperatures to
# explore the effect of the scale of bias and overall error bars.


# It is a good practice to specify the random seed to generate a reproducible simulation.
seed = 1717
np.random.seed(seed)

# Define some variables that correspond to the dimensionality of the problem
ntemps = 4  # Number of temperatures to simulate
ndim = calc.get_num_opt_params()  # Number of parameters
nwalkers = 2 * ndim  # Number of parallel walkers to simulate


##########################################################################################
# We start by instantiating :class:`~kliff.uq.MCMC`. This requires :class:`~kliff.loss.Loss`
# instance to construct the likelihood function. Additionally, we can specify the prior
# (or log-prior to be more precise), with the default be a uniform prior that is bounded
# over a finite range that we specify, or otherwise it will retrieve the bounds from the
# model (see :meth:`~kliff.models.KIMModel.set_opt_params`).
#
# To specify the temperatures to use, we can can use the arguments ``ntemps`` and
# ``Tmax_ratio`` to specify how many temperatures to simulate and the ratio of the highest
# temperature to the natural temperature :math:`T_0`, respectively. Alternatively, we can
# also give a list of the temperatures to use via ``Tladder`` argument.
#
# .. note::
#    It has been shown that including temperatures higher than :math:`T_0` helps the
#    convergence of walkers sampled at :math:`T_0`


# Create sampler
sampler = MCMC(loss, ntemps=4, threads=4, random=np.random.RandomState(seed))

# Initial starting point. This should be provided by the user.
bounds = calc.get_opt_params_bounds()
p0 = np.empty((4, 4, 2))
for ii, bound in enumerate(bounds):
    p0[:, :, ii] = np.random.uniform(*bound, (4, 4))

# Run MCMC
sampler.run_mcmc(p0, 10000)

# Retrieve the chain
chain = sampler.chain


#########################################################################################
# .. note::
#    The initial states need to be an array with shape ``(K, L, N,)``, where ``K`` is the
#    number of temperatures, ``L`` is the number of walkers, and ``N`` is the number of
#    parameters. As a default, ``M`` is set to be twice of ``N``, but we can also specify
#    it via the ``nwalkers`` argument when instantiating :class:`~kliff.uq.MCMC`.
#
# The resulting chains still need to be processed. First, we need to discard the first few
# iterations in the beginning of each chain as a burn-in time. This is similar to the
# equilibration time in a molecular dynamic simulation before we can start the
# measurement.
# KLIFF provides a function to estimate the burn-in time, based on the Marginal Standard
# Error Rule (MSER). This can be accessed via :func:`~kliff.uq.mcmc_utils.mser`.


# Estimate equilibration time using MSER for each temperature, walker, and dimension.
mser_array = np.empty((ntemps, nwalkers, ndim))
for tidx in range(ntemps):
    for widx in range(nwalkers):
        for pidx in range(ndim):
            mser_array[tidx, widx, pidx] = mser(
                chain[tidx, widx, :, pidx], dmin=0, dstep=10, dmax=-1
            )

burnin = int(np.max(mser_array))
print(f"Estimated burn-in time: {burnin}")


##########################################################################################
# .. note::
#    :func:`~kliff.uq.mcmc_utils.mser` only compute the estimation of the burn-in time for
#    one single temperature, walker, and parameter. Thus, we need to calculate the burn-in
#    time for each temperature, walker, and parameter separately.
#
# After discarding the first few iterations as the burn-in time, we only want to keep
# every :math:`\tau`-th iteration from the remaining chain, where :math:`\tau` is the
# autocorrelation length, to ensure uncorrelated samples.
# This calculation can be done using :func:`~kliff.uq.mcmc_utils.autocorr`.


# Estimate the autocorrelation length for each temperature
acorr_array = np.empty((ntemps, nwalkers, ndim))
for tidx in range(ntemps):
    acorr_array[tidx] = autocorr(chain[tidx], c=1, quiet=True)

thin = int(np.ceil(np.max(acorr_array)))
print(f"Estimated autocorrelation length: {thin}")


##########################################################################################
# .. note::
#    :func:`~kliff.uq.mcmc_utils.acorr` is a wrapper for emcee.autocorr.integrated_time_,
#    As such, the shape of the input array for this function needs to be ``(L, M, N,)``,
#    where ``L``, ``M``, and ``N`` are the number of walkers, steps, and parameters,
#    respectively. This also implies that we need to perform the calculation for each
#    temperature separately.
#
# Finally, after obtaining the independent samples, we need to assess whether the
# resulting samples have converged to a stationary distribution, and thus a good
# representation of the actual posterior. This is done by computing the potential scale
# reduction factor (PSRF). The PSRF declines to 1 as the number of iterations goes to
# infinity. A common threshold is about 1.1, but higher threshold has also been used.


# Assess the convergence for each temperature
samples = chain[:, :, burnin::thin]

threshold = 1.1  # Threshold for rhat
rhat_array = np.empty(ntemps)
for tidx in range(ntemps):
    rhat_array[tidx] = rhat(samples[tidx])

print(f"$\hat{{r}}^p$ values: {rhat_array}")


##########################################################################################
# .. note::
#    :func:`~kliff.uq.mcmc_utils.rhat` only computes the PSRF for one temperature, so that
#    the calculation needs to be carried on for each temperature separately.
#
# .. note::
#    As an alternative, KLIFF also provides a wrapper to emcee_.This can be accessed by
#    setting ``use_ptsampler=False`` when instantiating :class:`~kliff.uq.MCMC`. For
#    further documentation, see :class:`~kliff.uq.EmceeSampler`.
#
# .. _OpenKIM: https://openkim.org
# .. _ptemcee: https://github.com/willvousden/ptemcee
# .. _emcee: https://emcee.readthedocs.io
# .. _emcee.autocorr.integrated_time: https://emcee.readthedocs.io/en/stable/user/autocorr/#emcee.autocorr.integrated_time
