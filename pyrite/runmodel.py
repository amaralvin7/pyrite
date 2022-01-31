#!/usr/bin/env python3
import time
import data.data as data
import data.parameters as parameters
import framework
from unpacking import unpack_state_estimates
from ati import find_solution
import output
import budgets
from tools import merge_by_keys
import fluxes

start_time = time.time()

all_data = data.load_data()
poc_data = data.process_poc_data(all_data['POC'])
tracers = data.define_tracers(poc_data)
params = parameters.define_params(all_data['NPP'], 'NA')
residuals = data.define_residuals(params['P30']['prior'])
state_elements = framework.define_state_elements(tracers, params)
equation_elements = framework.define_equation_elements(tracers)
xo = framework.define_prior_vector(tracers, residuals, params)
Co = framework.define_cov_matrix(tracers, residuals, params)

ati_results = find_solution(tracers, state_elements, equation_elements, xo, Co)
xhat, Ckp1, convergence_evolution, cost_evolution = ati_results
estimates = unpack_state_estimates(tracers, params, state_elements, xhat, Ckp1)
tracer_estimates, residual_estimates, param_estimates = estimates

merge_by_keys(tracer_estimates, tracers)
merge_by_keys(param_estimates, params)
merge_by_keys(residual_estimates, residuals)

residuals_sym = budgets.get_symbolic_residuals(residuals)
residual_estimates_by_zone = budgets.integrate_by_zone(
    residuals_sym, state_elements, Ckp1, residuals=residuals)
merge_by_keys(residual_estimates_by_zone, residuals)

inventories_sym = budgets.get_symbolic_inventories(tracers)
inventories = budgets.integrate_by_zone_and_layer(
    inventories_sym, state_elements, Ckp1, tracers=tracers)

int_fluxes = fluxes.define_int_fluxes()
int_fluxes_sym = fluxes.get_symbolic_int_fluxes(int_fluxes)
int_flux_estimates = budgets.integrate_by_zone_and_layer(
    int_fluxes_sym, state_elements, Ckp1, tracers=tracers, params=params)
merge_by_keys(int_flux_estimates, int_fluxes)

output.write(params, residuals, inventories, int_fluxes)

print(f'--- {(time.time() - start_time)/60} minutes ---')