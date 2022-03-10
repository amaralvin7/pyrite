#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb  9 11:55:53 2021

@author: Vinicius J. Amaral

PYRITE Model (Particle cYcling Rates from Inversion of Tracers in the ocEan)

"""
import pickle
import sys
import time
import sympy as sym
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
import matplotlib.pyplot as plt
from matplotlib import ticker
from matplotlib.lines import Line2D
import mpl_toolkits.axisartist as AA
from mpl_toolkits.axes_grid1 import host_subplot

class PyriteModel:
    """A container for attributes and results of model runs.

    As defined, this model produces the results associated with inversions of
    real particulate organic carbon (POC) data as described in Amaral et al.
    (2021).
    """

    def __init__(self, args, priors_from='NA'):
        """Define basic model attributes and run the model.

        args -- a tuple of lists. The first list specifies gamma values,
        which are proportionality constants used for calculating the error in
        the residual terms in the model equations. The second list specifies
        relative errors, which are used for model parameters whose errors are
        poorly constrained (ws, wl, Bm1s, Bm1l, B3). The model is run for
        every gamma/RE combo.
        priors_from -- specifies whether to draw prior estimates and errors
        for B2p and Bm2 from NABE (Murnane et al., 1996) or OSP (Murnane 1994).
        """
        self.priors_from = priors_from
        self.gammas, self.rel_errs = args
        self.zg = 100  # grazing zone depth
        self.mld = 30  # mixed layer depth
        self.grid = [30, 50, 100, 150, 200, 330, 500]

        self.MOLAR_MASS_C = 12.011
        self.DAYS_PER_YEAR = 365.24

        self.load_data()
        self.define_tracers()
        self.define_fluxes()
        self.define_zones()

        self.model_runs = []
        for g in self.gammas:
            for re in self.rel_errs:
                run = PyriteModelRun(g, re)
                self.define_params(run)
                self.define_state_and_equation_elements(run)
                self.define_prior_vector_and_cov_matrix(run)
                xhat = self.ATI(run)
                self.calculate_residuals(xhat, run)
                int_resids = self.integrate_residuals(run)
                if str(self) != 'PyriteTwinX object':
                    inventories = self.calculate_inventories(run)
                    fluxes_sym = self.calculate_fluxes(run)
                    flux_names, int_fluxes = self.integrate_fluxes(
                        fluxes_sym, run)
                    self.calculate_res_times(
                        inventories, int_fluxes, int_resids, run)
                    self.calculate_timescales(
                        inventories, flux_names, int_fluxes, run)
                self.model_runs.append(run)

        self.pickle_model()

    def __repr__(self):

        return 'PyriteModel object'

    def load_data(self):
        """Load input data (must be from a file called 'pyrite_data.xlsx').

        After loading in data, calculate cruise-averaged POC concentrations.
        """
        self.data = pd.read_excel('pyrite_data.xlsx', sheet_name=None)

        poc_all = self.data['POC'].copy()
        depths = np.sort(poc_all['mod_depth'].unique())

        poc_means = pd.DataFrame(depths, columns=['depth'])

        poc_means['n_casts'] = poc_means.apply(
            lambda x: len(poc_all.loc[poc_all['mod_depth'] == x['depth']]),
            axis=1)

        for t in ('POCS', 'POCL'):
            poc_all.loc[poc_all[t] < 0, t] = 0
            poc_means[t] = poc_means.apply(
                lambda x: poc_all.loc[
                    poc_all['mod_depth'] == x['depth']][t].mean(), axis=1)
            poc_means[f'{t}_sd'] = poc_means.apply(
                lambda x: poc_all.loc[
                    poc_all['mod_depth'] == x['depth']][t].std(), axis=1)
            re_50m = float(poc_means.loc[
                poc_means['depth'] == 50, f'{t}_sd']
                / poc_means.loc[poc_means['depth'] == 50, t])
            poc_means.loc[poc_means['depth'] == 30, f'{t}_sd'] = (
                poc_means.loc[poc_means['depth'] == 30, t]*re_50m)
            poc_means[f'{t}_se'] = (poc_means[f'{t}_sd']
                                  / np.sqrt(poc_means['n_casts']))

        self.data['POC_means'] = poc_means.copy()

    def define_tracers(self):
        """Define tracers to be used in the model."""
        self.POCS = Tracer('POCS', self.data['POC_means'])
        self.POCL = Tracer('POCL', self.data['POC_means'])

        self.tracers = [self.POCS, self.POCL]

        self.tracer_names = [t.name for t in self.tracers]
        self.nte = len(self.tracers)*len(self.grid)

    def define_params(self, run):
        """Set prior estimates and errors of parameters for a given run."""
        P30_prior, P30_prior_e, Lp_prior, Lp_prior_e = self.process_npp_data()
        rel_err = run.rel_err

        if self.priors_from == 'NA':
            B2p_prior = (2/21) # m^3 mg^-1 y^-1
            B2p_prior_e = np.sqrt((0.2/21)**2 + (-1*(2/21**2))**2)
            Bm2_prior = 156  # y^-1
            Bm2_prior_e = 17
        else:
            B2p_prior = (0.8/1.57) # m^3 mg^-1 y^-1
            B2p_prior_e = np.sqrt((0.9/1.57)**2 + (-0.48*(0.8/1.57**2))**2)
            Bm2_prior = 400  # y^-1
            Bm2_prior_e = 10000

        run.ws = Param(2, 2*rel_err, 'ws', '$w_S$', 'm d$^{-1}$')
        run.wl = Param(20, 20*rel_err, 'wl', '$w_L$', 'm d$^{-1}$')
        run.B2p = Param(B2p_prior*self.MOLAR_MASS_C/self.DAYS_PER_YEAR,
                        B2p_prior_e*self.MOLAR_MASS_C/self.DAYS_PER_YEAR,
                        'B2p', '$\\beta^,_2$', 'm$^{3}$ mmol$^{-1}$ d$^{-1}$')
        run.Bm2 = Param(Bm2_prior/self.DAYS_PER_YEAR,
                        Bm2_prior_e/self.DAYS_PER_YEAR,
                        'Bm2', '$\\beta_{-2}$', 'd$^{-1}$')
        run.Bm1s = Param(0.1, 0.1*rel_err, 'Bm1s', '$\\beta_{-1,S}$',
                         'd$^{-1}$')
        run.Bm1l = Param(0.15, 0.15*rel_err, 'Bm1l', '$\\beta_{-1,L}$',
                         'd$^{-1}$')
        run.P30 = Param(P30_prior, P30_prior_e, 'P30', '$\.P_{S,ML}$',
                        'mmol m$^{-3}$ d$^{-1}$', depth_vary=False)
        run.Lp = Param(Lp_prior, Lp_prior_e, 'Lp', '$L_P$', 'm',
                       depth_vary=False)
        run.B3 = Param(0.06, 0.06*rel_err, 'B3', '$\\beta_3$', 'd$^{-1}$',
                       depth_vary=False)
        run.a = Param(0.3, 0.15, 'a', '$\\alpha$', depth_vary=False)
        run.zm = Param(500, 250, 'zm', '$z_m$', 'm', depth_vary=False)

        run.params = [run.ws, run.wl, run.B2p, run.Bm2, run.Bm1s, run.Bm1l,
                      run.P30, run.Lp, run.B3, run.a, run.zm]

        run.param_names = [p.name for p in run.params]

    def define_fluxes(self):
        """Define fluxes to be calculated."""
        self.sink_S = Flux('sink_S', '$w_SP_S$', 'POCS', 'ws')
        self.sink_L = Flux('sink_L', '$w_LP_L$', 'POCL', 'wl')
        self.sink_T = Flux('sink_T', '$w_TP_T$', None, 'wt')
        self.sinkdiv_S = Flux('sinkdiv_S', '$\\frac{d}{dz}w_SP_S$', 'POCS',
                              'ws', wrt=('POCS',))
        self.sinkdiv_L = Flux('sinkdiv_L', '$\\frac{d}{dz}w_LP_L$', 'POCL',
                              'wl', wrt=('POCL',))
        self.remin_S = Flux('remin_S', '$\\beta_{-1,S}P_S$', 'POCS', 'Bm1s',
                            wrt=('POCS',))
        self.remin_L = Flux('remin_L', '$\\beta_{-1,L}P_L$', 'POCL', 'Bm1l',
                            wrt=('POCL',))
        self.aggregation = Flux('aggregation', '$\\beta^,_2P^2_S$', 'POCS',
                            'B2p', wrt=('POCS', 'POCL'))
        self.disaggregation = Flux('disaggregation', '$\\beta_{-2}P_L$',
                                   'POCL', 'Bm2', wrt=('POCS', 'POCL'))
        self.production = Flux('production', '${\.P_S}$', 'POCS', None,
                               wrt=('POCS',))
        self.dvm = Flux('dvm', '$\\beta_3P_S$', 'POCS', 'B3',
                        wrt=('POCS', 'POCL'))

        self.fluxes = [self.sink_S, self.sink_L, self.sink_T, self.sinkdiv_S,
                       self.sinkdiv_L, self.remin_S, self.remin_L,
                       self.aggregation, self.disaggregation, self.production,
                       self.dvm]

    def process_npp_data(self):
        """Obtain prior estimates of particle production parameters.

        Lp -- vertical length scale of particle production.
        P30 -- production of small POC at the base of the mixed layer.
        """
        npp_data_raw = self.data['NPP']
        npp_data_clean = npp_data_raw.loc[(npp_data_raw['NPP'] > 0)]

        MIXED_LAYER_UPPER_BOUND, MIXED_LAYER_LOWER_BOUND = 28, 35

        npp_mixed_layer = npp_data_clean.loc[
            (npp_data_clean['target_depth'] >= MIXED_LAYER_UPPER_BOUND) &
            (npp_data_clean['target_depth'] <= MIXED_LAYER_LOWER_BOUND)]

        npp_below_mixed_layer = npp_data_clean.loc[
            npp_data_clean['target_depth'] >= MIXED_LAYER_UPPER_BOUND]

        P30_prior = npp_mixed_layer['NPP'].mean()/self.MOLAR_MASS_C
        P30_prior_e = npp_mixed_layer['NPP'].sem()/self.MOLAR_MASS_C

        npp_regression = smf.ols(
            formula='np.log(NPP/(P30_prior*self.MOLAR_MASS_C)) ~ target_depth',
            data=npp_below_mixed_layer).fit()

        Lp_prior = -1/npp_regression.params[1]
        Lp_prior_e = npp_regression.bse[1]/npp_regression.params[1]**2

        return P30_prior, P30_prior_e, Lp_prior, Lp_prior_e

    def define_zones(self):
        """Define the grid zones in the model."""
        self.zone_names = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
        self.zones = [GridZone(i, z) for i, z in enumerate(self.zone_names)]

    def define_state_and_equation_elements(self, run):
        """Define elements in the state vector and model equations."""
        self.state_elements = []
        self.equation_elements = []

        for s in self.tracer_names:
            for z in self.zones:
                self.state_elements.append(f'{s}_{z.label}')
                self.equation_elements.append(f'{s}_{z.label}')

        for s in self.tracer_names:
            for z in self.zones:
                self.state_elements.append(f'R{s}_{z.label}')  # residuals

        for p in run.params:
            if p.dv:
                for z in self.zone_names:
                    self.state_elements.append(f'{p.name}_{z}')
            else:
                self.state_elements.append(f'{p.name}')

    def define_prior_vector_and_cov_matrix(self, run):
        """Build the prior vector (xo) and covariance matrix (Co)"""
        xo = []
        Co = []

        for t in self.tracers:
            xo.extend(t.prior['conc'])
            Co.extend(t.prior['conc_e']**2)

        for t in self.tracer_names:
            xo.extend(np.zeros(len(self.grid)))
            if 'POC' in t:
                Co.extend(np.ones(len(self.grid))*(
                    run.gamma*run.P30.prior*self.mld)**2)

        for p in run.params:
            if p.dv:
                xo.extend(np.ones(len(self.grid))*p.prior)
                Co.extend(np.ones(len(self.grid))*p.prior_e**2)
            else:
                xo.append(p.prior)
                Co.append(p.prior_e**2)

        self.xo = np.array(xo)
        run.Co = np.diag(Co)

    def slice_by_tracer(self, to_slice, tracer):
        """Return a slice of a list that corresponds to a given tracer.

        to_slice -- list from which to take a slice.
        tracer -- function returns list slice correpsonding to this tracer.
        """
        sliced = [to_slice[i] for i, e in enumerate(
            self.state_elements) if e.split('_')[0] == tracer]

        return sliced

    def previous_zone(self, zone_name):
        """Return the zone above that which is specified by zone_name"""
        return self.zone_names[self.zone_names.index(zone_name) - 1]

    def equation_builder(self, species, zone, params_known=None):
        """Return the model equation for a species at a depth index.

        species -- string label for any equation element.
        zone -- GridZone object associated with the species element.
        params_known -- a dictionary of parameters from which to draw from,
        should only exist if function is evoked from a TwinX object.
        """
        zim1, zi = zone.depths
        h = zone.thick
        z = zone.label
        if z == 'A':
            Psi = sym.symbols('POCS_A')
            Pli = sym.symbols('POCL_A')

        else:
            pz = self.previous_zone(z)
            Psi, Psim1 = sym.symbols(f'POCS_{z} POCS_{pz}')
            Pli, Plim1 = sym.symbols(f'POCL_{z} POCL_{pz}')
            Psa = (Psi + Psim1)/2
            Pla = (Pli + Plim1)/2

        if not params_known:
            Bm2 = sym.symbols(f'Bm2_{z}')
            B2p = sym.symbols(f'B2p_{z}')
            Bm1s = sym.symbols(f'Bm1s_{z}')
            Bm1l = sym.symbols(f'Bm1l_{z}')
            ws = sym.symbols(f'ws_{z}')
            wl = sym.symbols(f'wl_{z}')
            P30 = sym.symbols('P30')
            Lp = sym.symbols('Lp')
            RPsi = sym.symbols(f'RPOCS_{z}')
            RPli = sym.symbols(f'RPOCL_{z}')
            B3 = sym.symbols('B3')
            a = sym.symbols('a')
            D = sym.symbols('zm')
            if zone.label != 'A':
                wsm1 = sym.symbols(f'ws_{pz}')
                wlm1 = sym.symbols(f'wl_{pz}')
        else:
            Bm2 = params_known['Bm2'][z]['est']
            B2p = params_known['B2p'][z]['est']
            Bm1s = params_known['Bm1s'][z]['est']
            Bm1l = params_known['Bm1l'][z]['est']
            P30 = params_known['P30']['est']
            Lp = params_known['Lp']['est']
            ws = params_known['ws'][z]['est']
            wl = params_known['wl'][z]['est']
            RPsi = params_known['POCS'][z][0]
            RPli = params_known['POCL'][z][0]
            B3 = params_known['B3']['est']
            a = params_known['a']['est']
            D = params_known['zm']['est']
            if zone.label != 'A':
                wsm1 = params_known['ws'][pz]['est']
                wlm1 = params_known['wl'][pz]['est']

        if species == 'POCS':
            if z == 'A':
                eq = (-ws*Psi + Bm2*Pli*h - (B2p*Psi + Bm1s)*Psi*h + RPsi
                      - B3*Psi*h)
                if not params_known:
                    eq += P30*self.mld
            else:
                eq = (-ws*Psi + wsm1*Psim1 + Bm2*Pla*h
                      - (B2p*Psa + Bm1s)*Psa*h + RPsi)
                if z in ('B', 'C'):
                    eq += -B3*Psa*h
                if not params_known:
                    eq += Lp*P30*(sym.exp(-(zim1 - self.mld)/Lp)
                                  - sym.exp(-(zi - self.mld)/Lp))
        else:
            if z == 'A':
                eq = -wl*Pli + B2p*Psi**2*h - (Bm2 + Bm1l)*Pli*h + RPli
            else:
                eq = -wl*Pli + wlm1*Plim1 + B2p*Psa**2*h - (Bm2 + Bm1l)*Pla*h + RPli
                if z in ('D', 'E', 'F', 'G'):
                    zg = self.zg
                    Ps_A, Ps_B, Ps_C = sym.symbols('POCS_A POCS_B POCS_C')
                    zoneA, zoneB, zoneC = self.zones[:3]
                    B3Ps_av = (B3/zg)*(Ps_A*zoneA.thick
                                       + (Ps_A + Ps_B)/2*zoneB.thick
                                       + (Ps_B + Ps_C)/2*zoneC.thick)
                    co = np.pi/(2*(D - zg))*a*zg
                    eq += B3Ps_av*co*((D - zg)/np.pi*(
                            sym.cos(np.pi*(zim1 - zg)/(D - zg))
                            - sym.cos(np.pi*(zi - zg)/(D - zg))))
        return eq

    def extract_equation_variables(self, y, v):
        """Return symbolic and numerical values of variables in an equation.

        y -- a symbolic equation.
        v -- list of values from which to draw numerical values from.
        """
        x_symbolic = list(y.free_symbols)
        x_numerical = []
        x_indices = []

        for x in x_symbolic:
            element_index = self.state_elements.index(x.name)
            x_indices.append(element_index)
            x_numerical.append(v[element_index])

        return x_symbolic, x_numerical, x_indices

    def evaluate_model_equations(self, v, return_F=False, params_known=None):
        """Evaluates model equations, and Jacobian matrix (if specified).

        v -- list of values from which to draw numerical values.
        return_F -- True if the Jacobian matrix should be returned.
        params_known -- a dictionary of parameters from which to draw from,
        should only exist if function is evoked from a TwinX object.
        """
        f = np.zeros(self.nte)
        if params_known:
            F = np.zeros((self.nte, self.nte))
        else:
            F = np.zeros((self.nte, len(self.state_elements)))

        for i, element in enumerate(self.equation_elements):
            species, zone_name = element.split('_')
            zone = self.zones[self.zone_names.index(zone_name)]
            y = self.equation_builder(species, zone, params_known=params_known)
            x_sym, x_num, x_ind = self.extract_equation_variables(y, v)
            f[i] = sym.lambdify(x_sym, y)(*x_num)
            if return_F:
                for j, x in enumerate(x_sym):
                    dy = y.diff(x)
                    dx_sym, dx_num, _ = self.extract_equation_variables(dy, v)
                    F[i, x_ind[j]] = sym.lambdify(dx_sym, dy)(*dx_num)

        if return_F:
            return f, F
        return f

    def eval_symbolic_func(self, run, y, err=True, cov=True):
        """Evaluate a symbolic function using results from a given run.

        run -- model run whose results are being calculated.
        y -- the symbolic function (i.e., expression).
        err -- True if errors should be propagated (increases runtime).
        cov -- True if covarainces between state variables should be
        considered (increases runtime).
        result -- the numerical result
        error -- the numerical error
        """
        x_symbolic = list(y.free_symbols)
        x_numerical = []
        x_indices = []
        for x in x_symbolic:
            x_indices.append(self.state_elements.index(x.name))
            if '_' in x.name:  # if it varies with depth
                element, zone = x.name.split('_')
                if element in self.tracer_names:  # if it's a tracer
                    di = self.zone_names.index(zone)
                    x_numerical.append(
                        run.tracer_results[element]['est'][di])
                elif element[1:] in self.tracer_names:  # if it's a residual
                    x_numerical.append(
                        run.integrated_resids[element[1:]][zone][0])
                else:  # if it's a depth-varying parameter
                    x_numerical.append(
                        run.param_results[element][zone]['est'])
            else:  # if it's a depth-independent parameter
                x_numerical.append(run.param_results[x.name]['est'])

        result = sym.lambdify(x_symbolic, y)(*x_numerical)

        if err is False:
            return result

        variance_sym = 0  # symbolic expression for variance of y
        derivs = [y.diff(x) for x in x_symbolic]
        cvm = run.cvm[  # sub-CVM corresponding to state elements in y
            np.ix_(x_indices, x_indices)]
        for i, row in enumerate(cvm):
            for j, _ in enumerate(row):
                if i > j:
                    continue
                if i == j:
                    variance_sym += (derivs[i]**2)*cvm[i, j]
                else:
                    if cov:
                        variance_sym += 2*derivs[i]*derivs[j]*cvm[i, j]
        variance = sym.lambdify(x_symbolic, variance_sym)(*x_numerical)
        error = np.sqrt(variance)

        return result, error

    def ATI(self, run):
        """Algorithm of total inversion, returns a vector of state estimates.

        run -- model run whose results are being calculated.
        xhat -- vector that holds estimates of the state elements
        (i.e., the solution vector).

        See: Tarantola A, Valette B. 1982. Generalized nonlinear inverse
        problems solved using the least squares criterion. Reviews of
        Geophysics and Space Physics 20(2): 219–232.
        doi:10.1029/RG020i002p00219.
        """
        def calculate_xkp1(xk, f, F):
            """For iteration k, return a new estimate of the state vector.

            Also returns a couple  of matrices for future calculations.
            xk -- the state vector estimate at iteration k
            xkp1 -- the state vector estimate at iteration k+1
            f -- vector of model equations
            F -- Jacobian matrix
            """
            CoFT = run.Co @ F.T
            FCoFT = F @ CoFT
            FCoFTi = np.linalg.inv(FCoFT)
            xkp1 = self.xo + CoFT @ FCoFTi @ (F @ (xk - self.xo) - f)

            return xkp1, CoFT, FCoFTi

        def check_convergence(xk, xkp1):
            """Return whether or not the ATI has converged after an iteration.

            Convergence is reached if every variable in xkp1 changes by less
            than 0.0001% relative to its estimate in xk.
            """
            converged = False
            max_change_limit = 10**-6
            change = np.abs((xkp1 - xk)/xk)
            run.convergence_evolution.append(np.max(change))

            if np.max(change) < max_change_limit:
                converged = True

            return converged

        def calculate_cost(x):
            """Calculate the cost at a given iteration"""
            cost = (x - self.xo).T @ np.linalg.inv(run.Co) @ (x - self.xo)

            run.cost_evolution.append(cost)

        def find_solution():
            """Iteratively finds a solution of the state vector."""
            max_iterations = 100

            xk = self.xo
            xkp1 = np.ones(len(xk))  # at iteration k+1
            for count in range(max_iterations):
                f, F = self.evaluate_model_equations(xk, return_F=True)
                xkp1, CoFT, FCoFTi = calculate_xkp1(xk, f, F)
                calculate_cost(xkp1)
                if count > 0:
                    run.converged = check_convergence(xk, xkp1)
                    if run.converged:
                        break
                xk = xkp1

            return F, xkp1, CoFT, FCoFTi

        def unpack_state_estimates():
            """Unpack estimates and errors of state elements for later use."""
            F, xkp1, CoFT, FCoFTi = find_solution()

            Ckp1 = run.Co - CoFT @ FCoFTi @ F @ run.Co

            run.cvm = Ckp1
            xhat = xkp1
            xhat_e = np.sqrt(np.diag(Ckp1))

            for t in self.tracer_names:
                run.tracer_results[t] = {
                    'est': self.slice_by_tracer(xhat, t),
                    'err': self.slice_by_tracer(xhat_e, t)}
                run.integrated_resids[t] = {}
                for z in self.zone_names:
                    run.integrated_resids[t][z] = (
                        xhat[self.state_elements.index(f'R{t}_{z}')],
                        xhat_e[self.state_elements.index(f'R{t}_{z}')])

            for param in run.params:
                p = param.name
                if param.dv:
                    run.param_results[p] = {}
                    for z in self.zone_names:
                        zone_param = '_'.join([p, z])
                        i = self.state_elements.index(zone_param)
                        run.param_results[p][z] = {'est': xhat[i],
                                                   'err': xhat_e[i]}
                else:
                    i = self.state_elements.index(p)
                    run.param_results[p] = {'est': xhat[i],
                                            'err': xhat_e[i]}

            return xhat

        return unpack_state_estimates()

    def calculate_residuals(self, xhat, run):
        """Calculate data and equation residuals.

        Also, check that equations are satisfied."""
        run.x_resids = (xhat - self.xo)/np.sqrt(np.diag(run.Co))
        eq_resids = self.evaluate_model_equations(xhat)
        for el in self.tracer_names:
            run.f_check[el] = self.slice_by_tracer(eq_resids, el)

    def calculate_inventories(self, run):
        """Calculate inventories of the model tracers in each grid zone."""
        inventory_sym = {}
        zone_dict = {'EZ': self.zones[:3], 'UMZ': self.zones[3:]}

        for t in self.tracer_names:
            run.inventories[t] = {}
            inventory_sym[t] = {}
            for sz in zone_dict:
                sz_inventory = 0
                for zone in zone_dict[sz]:
                    z = zone.label
                    h = zone.thick
                    if z == 'A':
                        t_sym = sym.symbols(f'{t}_{z}')
                    else:
                        pz = self.previous_zone(z)
                        ti, tim1 = sym.symbols(f'{t}_{z} {t}_{pz}')
                        t_sym = (ti + tim1)/2
                    z_inventory = t_sym*h
                    run.inventories[t][z] = self.eval_symbolic_func(run, z_inventory)
                    inventory_sym[t][z] = z_inventory
                    sz_inventory += z_inventory
                run.inventories[t][sz] = self.eval_symbolic_func(run, sz_inventory)
                inventory_sym[t][sz] = sz_inventory

        return inventory_sym

    def calculate_fluxes(self, run=None):
        """Calculate profiles of all model fluxes."""

        fluxes_sym = {}

        for flux in self.fluxes:
            f = flux.name
            if run:
                run.flux_profiles[f] = {'est': [], 'err': []}
            if flux.wrt:
                fluxes_sym[f] = []
            for zone in self.zones:
                z = zone.label
                zim1, zi = zone.depths
                h = zone.thick
                if 'div' in f:
                    wi, ti = sym.symbols(f'{flux.param}_{z} {flux.tracer}_{z}')
                    if z == 'A':
                        y = wi*ti
                    else:
                        pz = self.previous_zone(z)
                        wim1, tim1 = sym.symbols(
                            f'{flux.param}_{pz} {flux.tracer}_{pz}')
                        y = wi*ti - wim1*tim1
                    y_discrete = y/h
                elif f == 'production':
                    P30, Lp = sym.symbols('P30 Lp')
                    if z == 'A':
                        y = P30*self.mld
                    else:
                        y = Lp*P30*(sym.exp(-(zim1 - self.mld)/Lp)
                                    - sym.exp(-(zi - self.mld)/Lp))
                    y_discrete = P30*sym.exp(-(zi - self.mld)/Lp)
                elif f == 'dvm':
                    B3 = sym.symbols('B3')
                    a = sym.symbols('a')
                    D = sym.symbols('zm')
                    if z in ('A', 'B', 'C'):
                        ti = sym.symbols(f'POCS_{z}')
                        if z == 'A':
                            y = B3*ti*h
                        else:
                            tim1 = sym.symbols(f'POCS_{self.previous_zone(z)}')
                            t_av = (ti + tim1)/2
                            y = B3*t_av*h
                    else:
                        zg = self.zg
                        Ps_A, Ps_B, Ps_C = sym.symbols('POCS_A POCS_B POCS_C')
                        zoneA, zoneB, zoneC = self.zones[:3]
                        B3Ps_av = (B3/zg)*(Ps_A*zoneA.thick
                                           + (Ps_A + Ps_B)/2*zoneB.thick
                                           + (Ps_B + Ps_C)/2*zoneC.thick)
                        co = np.pi/(2*(D - zg))*a*zg
                        y = B3Ps_av*co*((D - zg)/np.pi*(
                                sym.cos(np.pi*(zim1 - zg)/(D - zg))
                                - sym.cos(np.pi*(zi - zg)/(D - zg))))
                    y_discrete = y/h
                elif 'sink_' in f:
                    if f[-1] == 'T':
                        wsi = f'ws_{z}'
                        wli = f'wl_{z}'
                        Psi = f'POCS_{z}'
                        Pli = f'POCL_{z}'
                        ws, wl, Ps, Pl = sym.symbols(
                            f'{wsi} {wli} {Psi} {Pli}')
                        y_discrete = ws*Ps + wl*Pl
                    else:
                        wi, ti = sym.symbols(
                            f'{flux.param}_{z} {flux.tracer}_{z}')
                        y_discrete = wi*ti
                else:
                    if f == 'aggregation':
                        order = 2
                    else:
                        order = 1
                    pi, ti = sym.symbols(f'{flux.param}_{z} {flux.tracer}_{z}')
                    if z == 'A':
                        y = pi*ti**order*h
                    else:
                        pz = self.previous_zone(z)
                        tim1 = sym.symbols(f'{flux.tracer}_{pz}')
                        t_av = (ti + tim1)/2
                        y = pi*t_av**order*h
                    y_discrete = y/h
                if run:
                    est, err = self.eval_symbolic_func(run, y_discrete)
                    run.flux_profiles[f]['est'].append(est)
                    run.flux_profiles[f]['err'].append(err)
                if flux.wrt:
                    fluxes_sym[f].append(y)

        return fluxes_sym

    def integrate_fluxes(self, fluxes_sym, run=None):
        """Integrate fluxes within each model grid zone."""

        fluxes = fluxes_sym.keys()
        flux_integrals_sym = {}

        zone_dict = {'EZ': self.zone_names[:3], 'UMZ': self.zone_names[3:]}

        for f in fluxes:
            flux_integrals_sym[f] = {}
            if run:
                run.flux_integrals[f] = {}
            for sz, zones in zone_dict.items():
                to_integrate = 0
                for z in zones:
                    zone_flux = fluxes_sym[f][self.zone_names.index(z)]
                    to_integrate += zone_flux
                    if run:
                        run.flux_integrals[f][z] = self.eval_symbolic_func(
                            run, zone_flux)
                        flux_integrals_sym[f][z] = zone_flux
                flux_integrals_sym[f][sz] = to_integrate
                if run:
                    run.flux_integrals[f][sz] = self.eval_symbolic_func(
                        run, to_integrate)

        return fluxes, flux_integrals_sym

    def integrate_residuals(self, run):
        """Integrate model equation residuals within each model grid zone."""

        zone_dict = {'EZ': self.zone_names[:3], 'UMZ': self.zone_names[3:]}

        int_resids_sym = {}

        for t in self.tracer_names:
            int_resids_sym[t] = {}
            for sz, zones in zone_dict.items():
                to_integrate = 0
                for z in zones:
                    int_resids_sym[t][z] = sym.symbols(f'R{t}_{z}')
                    to_integrate += sym.symbols(f'R{t}_{z}')
                int_resids_sym[t][sz] = to_integrate
                run.integrated_resids[t][sz] = self.eval_symbolic_func(
                    run, to_integrate)

        return int_resids_sym

    def calculate_res_times(self, invent_sym, flux_int_sym, int_resids, run):
        """Calculate residence times of traces in each model grid zone."""

        fluxes_in = {'POCS': ['production', 'disaggregation'],
                     'POCL': ['aggregation']}

        for t in self.tracer_names:
            run.res_times[t] = {}
            sf = t[-1]
            for z in invent_sym[t].keys():
                inventory = invent_sym[t][z]
                sum_of_fluxes = 0
                for f in fluxes_in[t]:
                    sum_of_fluxes += flux_int_sym[f][z]
                if run.flux_integrals[f'sinkdiv_{sf}'][z][0] < 0:
                    sum_of_fluxes += -flux_int_sym[f'sinkdiv_{sf}'][z]
                if run.integrated_resids[t][z][0] > 0:
                    sum_of_fluxes += int_resids[t][z]
                if t == 'POCL' and z in ('UMZ', 'D', 'E', 'F', 'G'):
                    sum_of_fluxes += flux_int_sym['dvm'][z]
                run.res_times[t][z] = self.eval_symbolic_func(
                    run, inventory/sum_of_fluxes)

    def calculate_timescales(self, inventory_sym, fluxes, flux_int_sym, run):
        """Calculate turnover timescales associated with each model flux."""

        for t in self.tracer_names:
            run.timescales[t] = {}
            for z in inventory_sym[t].keys():
                run.timescales[t][z] = {}
                for f in fluxes:
                    if t in eval(f'self.{f}.wrt'):
                        run.timescales[t][z][f] = (self.eval_symbolic_func(
                            run, inventory_sym[t][z]/flux_int_sym[f][z]))

    def pickle_model(self):
        """Pickle (save) the model for future plotting and analysis."""

        if str(self) == 'PyriteTwinX object':
            prefix = 'out/POC_twinX_'
        else:
            prefix = 'out/POC_modelruns_'

        s = prefix + f'dvmTrue_{self.priors_from}.pkl'

        with open(s, 'wb') as file:
            pickle.dump(self, file)

class Tracer:
    """Container for metadata of model tracers."""

    def __init__(self, name, data):

        self.name = name
        self.prior = data[['depth', name, f'{name}_se']].copy()
        self.prior.rename(columns={self.prior.columns[1]: 'conc',
                                   self.prior.columns[2]: 'conc_e'},
                          inplace=True)

    def __repr__(self):

        return f'Tracer({self.name})'


class Param:
    """Container for metadata of model parameters."""

    def __init__(self, prior, prior_error, name, label, units=None,
                 depth_vary=True):

        self.prior = prior
        self.prior_e = prior_error
        self.name = name
        self.label = label
        self.units = units
        self.dv = depth_vary

    def __repr__(self):

        return f'Param({self.name})'


class GridZone:
    """Container for metadata of model grid zones."""

    def __init__(self, index, label):

        self.label = label
        self.depths = [0, 30, 50, 100, 150, 200, 330, 500][index:index + 2]
        self.thick = self.depths[1] - self.depths[0]

    def __repr__(self):

        return f'GridZone({self.label})'


class Flux:
    """Container for metadata of model fluxes."""

    def __init__(self, name, label, tracer, param, wrt=None):

        self.name = name
        self.label = label
        self.tracer = tracer
        self.param = param
        self.wrt = wrt

    def __repr__(self):

        return f'Flux({self.name})'


class PyriteModelRun():
    """Container for storing the results of a model run."""

    def __init__(self, gamma, rel_err):
        """Defines model data to be stored."""
        self.gamma = gamma
        self.rel_err = rel_err
        self.cost_evolution = []
        self.convergence_evolution = []
        self.converged = False
        self.cvm = None
        self.tracer_results = {}
        self.param_results = {}
        self.x_resids = None
        self.f_check = {}
        self.inventories = {}
        self.integrated_resids = {}
        self.flux_profiles = {}
        self.flux_integrals = {}
        self.res_times = {}
        self.timescales = {}

    def __repr__(self):

        return f'PyriteModelRun(gamma={self.gamma}, re={self.rel_err})'


class PyriteTwinX(PyriteModel):
    """Twin experiment class for PyriteModel.

    Verifies that the model is able to produce accurate estimates of the state
    elements. Inherits from the PyriteModel class. load_data() is the only
    method that is practically overridden. Other methods that are inherited
    but currently unused are labeled as such in their docstrings.
    """

    def __init__(self, args, pickled_model=None):
        """Build a PyriteModel with gamma values to be used for the TwinX.

        args -- a tuple of two one-element lists. Specifies values with which
        to perform twin experiments. First value is gamma, second is relative
        error.
        self.pickled_model -- the pickled model from which to draw results to
        generate pseudodata.
        """
        self.pickled_model = pickled_model
        self.gamma, self.rel_err = args
        with open(self.pickled_model, 'rb') as file:
            self.model = pickle.load(file)

        super().__init__(args, priors_from=self.model.priors_from)

    def __repr__(self):

        return 'PyriteTwinX object'

    def load_data(self):
        """Use results from self.pickled_model to generate pseudodata."""
        self.get_target_values()
        x = self.generate_pseudodata()

        self.data = self.model.data.copy()
        tracer_data = self.data['POC_means'].copy()
        for t in ('POCS', 'POCL'):
            re = tracer_data[f'{t}_se']/tracer_data[t]
            tracer_data[t] = self.model.slice_by_tracer(x, t)
            tracer_data[f'{t}_se'] = tracer_data[t]*re

            self.data['POC_means'] = tracer_data.copy()

    def get_target_values(self):
        """Get the target values with which to generate pseudodata"""
        for r in self.model.model_runs:
            if (r.gamma == self.gamma[0]) and (r.rel_err == self.rel_err[0]):
                target_params = r.param_results.copy()
                target_resids = r.integrated_resids.copy()
                self.target_values = {**target_params, **target_resids}
                break

    def generate_pseudodata(self):
        """Generate pseudodata from the model equations."""

        def generate_linear_solution():
            """Obtain estimates of the tracers with a least-squares approach.

            Uses linear formulations of the model equations, which require
            a first-order aggregation term and the assumption of perfectly-
            known particle production. DVM and residuals are neglected here.
            """
            A = np.zeros((self.model.nte, self.model.nte))
            b = np.zeros(self.model.nte)
            element_index = self.model.state_elements

            for i, element in enumerate(element_index[:self.model.nte]):

                species, z = element.split('_')
                for zo in self.model.zones:
                    if zo.label == z:
                        zone = zo
                        break
                zim1, zi = zone.depths
                h = zone.thick

                iPsi = element_index.index(f'POCS_{z}')
                iPli = element_index.index(f'POCL_{z}')

                if z != 'A':
                    pz = self.model.previous_zone(z)
                    iPsim1 = element_index.index(f'POCS_{pz}')
                    iPlim1 = element_index.index(f'POCL_{pz}')

                B2 = 0.8/self.model.DAYS_PER_YEAR
                Bm2 = self.target_values['Bm2'][z]['est']
                Bm1s = self.target_values['Bm1s'][z]['est']
                Bm1l = self.target_values['Bm1l'][z]['est']
                P30 = self.target_values['P30']['est']
                Lp = self.target_values['Lp']['est']
                ws = self.target_values['ws'][z]['est']
                wl = self.target_values['wl'][z]['est']
                if zone.label != 'A':
                    wsm1 = self.target_values['ws'][pz]['est']
                    wlm1 = self.target_values['wl'][pz]['est']

                if species == 'POCS':
                    if z == 'A':
                        A[i, iPsi] = ws + (Bm1s + B2)*h
                        A[i, iPli] = -Bm2*h
                        b[i] = P30*self.model.mld
                    else:
                        A[i, iPsi] = ws + 0.5*(Bm1s + B2)*h
                        A[i, iPsim1] = -wsm1 + 0.5*(Bm1s + B2)*h
                        A[i, iPli] = -0.5*Bm2*h
                        A[i, iPlim1] = -0.5*Bm2*h
                        b[i] = Lp*P30*(np.exp(-(zim1 - self.model.mld)/Lp)
                                       - np.exp(-(zi - self.model.mld)/Lp))
                else:
                    if z == 'A':
                        A[i, iPli] = wl + (Bm1l + Bm2)*h
                        A[i, iPsi] = -B2*h
                    else:
                        A[i, iPli] = wl + 0.5*(Bm1l + Bm2)*h
                        A[i, iPlim1] = -wlm1 + 0.5*(Bm1l + Bm2)*h
                        A[i, iPsi] = -0.5*B2*h
                        A[i, iPsim1] = -0.5*B2*h

            x = np.linalg.solve(A, b)
            x = np.clip(x, 10**-10, None)

            return x

        def generate_nonlinear_solution():
            """Obtain estimates of the tracers with an iterative approach.

            Takes the previously generated solution to the linear model
            equations and uses it as a prior estimate in an iterative approach
            to obtain estimates of the model tracers from the nonlinear
            model equations that are considered in the real data inversions.
            """
            max_iterations = 20
            max_change_limit = 10**-6
            xk = generate_linear_solution()

            P30 = self.target_values['P30']['est']
            Lp = self.target_values['Lp']['est']
            b = np.zeros(self.model.nte)
            for i, z in enumerate(self.model.zones):
                zim1, zi = z.depths
                if z.label == 'A':
                    b[i] = -P30*self.model.mld
                else:
                    b[i] = -Lp*P30*(np.exp(-(zim1 - self.model.mld)/Lp)
                                    - np.exp(-(zi - self.model.mld)/Lp))

            for _ in range(max_iterations):
                f, F = self.model.evaluate_model_equations(
                    xk, return_F=True, params_known=self.target_values)
                xkp1 = np.linalg.solve(F, (F @ xk - f + b))
                change = np.abs((xkp1 - xk)/xk)
                if np.max(change) < max_change_limit:
                    break
                xk = xkp1

            Ps = xkp1[:7]
            Pl = xkp1[7:14]

            fig, [ax1, ax2] = plt.subplots(1, 2, tight_layout=True)
            fig.subplots_adjust(wspace=0.5)

            ax1.set_xlabel('$P_{S}$ (mmol m$^{-3}$)', fontsize=14)
            ax2.set_xlabel('$P_{L}$ (mmol m$^{-3}$)', fontsize=14)
            ax1.set_ylabel('Depth (m)', fontsize=14)

            ax1.scatter(Ps, self.model.grid)
            ax2.scatter(Pl, self.model.grid)

            for ax in (ax1, ax2):
                ax.invert_yaxis()

            suffix = f'_{self.model.priors_from}_dvmTrue'
            fig.savefig(f'out/fwd_POC{suffix}')
            plt.close()

            return xkp1, b

        xkp1, b = generate_nonlinear_solution()

        self.pseudo_check = self.model.evaluate_model_equations(
            xkp1, params_known=self.target_values) - b

        return xkp1

    def define_fluxes(self):
        """Unused"""
        pass

    def calculate_inventories(self):
        """Unused"""
        pass

    def calculate_fluxes(self):
        """Unused"""
        pass

    def integrate_fluxes(self):
        """Unused"""
        pass

    def calculate_timescales(self):
        """Unused"""
        pass


class PlotterTwinX():
    """Generates all twin experiment plots."""

    def __init__(self, pickled_model):

        with open(pickled_model, 'rb') as file:
            self.model = pickle.load(file)

        if str(self.model) == 'PyriteTwinX object':
            self.is_twinX = True
        else:
            self.is_twinX = False

        self.define_colors()

        priors_str = self.model.priors_from
        dvm_str = 'dvmTrue'

        for run in self.model.model_runs:

            gamma_str = f'gam{str(run.gamma).replace(".","p")}'
            re_str = f're{str(run.rel_err).replace(".","p")}'
            suffix = f'_{priors_str}_{dvm_str}_{re_str}_{gamma_str}'

            self.cost_and_convergence(run, suffix)
            self.params(run, suffix)
            self.poc_profiles(run, suffix)
            self.residual_pdfs(run, suffix)
            self.residual_profiles(run, suffix)

    def define_colors(self):

        self.black = '#000000'
        self.orange = '#E69F00'
        self.sky = '#56B4E9'
        self.green = '#009E73'
        self.blue = '#0072B2'
        self.vermillion = '#D55E00'
        self.radish = '#CC79A7'
        self.white = '#FFFFFF'

        self.colors = (
            self.black, self.orange, self.sky, self.green,
            self.blue, self.vermillion, self.radish)

    def cost_and_convergence(self, run, suffix):

        k = len(run.cost_evolution)

        fig, ax = plt.subplots(1, tight_layout=True)
        ax.plot(np.arange(2, k+1), run.convergence_evolution,
                marker='o', ms=3, c=self.blue)
        ax.set_yscale('log')
        ax.set_xlabel('Iteration, $k$', fontsize=16)
        ax.set_ylabel('max'+r'$(\frac{|x_{i,k+1}-x_{i,k}|}{x_{i,k}})$',
                      fontsize=16)

        filename = f'out/conv{suffix}'
        if self.is_twinX:
            filename += '_TE'
        fig.savefig(f'{filename}.png')
        plt.close()

        fig, ax = plt.subplots(1, tight_layout=True)
        ax.plot(np.arange(1, k+1), run.cost_evolution, marker='o', ms=3,
                c=self.blue)
        ax.set_xlabel('Iteration, $k$', fontsize=16)
        ax.set_ylabel('Cost, $J$', fontsize=16)

        filename = f'out/cost{suffix}'
        if self.is_twinX:
            filename += '_TE'
        fig.savefig(f'{filename}.png')
        plt.close()

    def params(self, run, suffix):

        dv, ((ax1, ax2, ax3), (ax4, ax5, ax6)) = plt.subplots(2, 3)
        dv_axs = ax1, ax2, ax3, ax4, ax5, ax6

        dc, ((ax7, ax8, ax9), (ax10, ax11, ax12)) = plt.subplots(
            2, 3, tight_layout=True)
        dc_axs = ax7, ax8, ax9, ax10, ax11
        ax12.axis('off')

        dv.text(0.05, 0.5, 'Depth (m)', fontsize=14, ha='center',
                va='center', rotation='vertical')
        dv.subplots_adjust(left=0.14, right=0.95, top=0.95, bottom=0.15,
                           hspace=0.5)

        dv_params = [p for p in run.params if p.dv]
        dc_params = [p for p in run.params if not p.dv]

        for i, param in enumerate(dv_params):
            p = param.name
            ax = dv_axs[i]
            ax.set_xlabel(f'{param.label} ({param.units})', fontsize=12)
            if i not in (0,3):
                ax.tick_params(labelleft=False)
            ax.invert_yaxis()
            ax.set_ylim(top=0, bottom=530)
            ax.tick_params(axis='both', which='major', labelsize=12)
            ax.axvline(param.prior, c=self.blue, lw=1.5, ls=':')
            ax.axvline(param.prior - param.prior_e, c=self.blue, lw=1.5,
                       ls='--')
            ax.axvline(param.prior + param.prior_e, c=self.blue, lw=1.5,
                       ls='--')
            for j, z in enumerate(self.model.zone_names):
                zone = self.model.zones[j]
                if 'w' in p:
                    depth = zone.depths[1]
                    ax.errorbar(
                        run.param_results[p][z]['est'], depth, fmt='o', ms=8,
                        xerr=run.param_results[p][z]['err'],
                        ecolor=self.orange, elinewidth=1, c=self.orange,
                        capsize=6, fillstyle='none', zorder=3,
                        markeredgewidth=1)
                else:
                    depths = zone.depths
                    depth = np.mean(depths)
                    ax.scatter(
                        run.param_results[p][z]['est'], depth, marker='o',
                        c=self.orange, s=14, zorder=3)
                    ax.fill_betweenx(
                        depths,
                        (run.param_results[p][z]['est']
                          - run.param_results[p][z]['err']),
                        (run.param_results[p][z]['est']
                          + run.param_results[p][z]['err']),
                        color=self.orange, alpha=0.25)

                if self.is_twinX:
                    ax.scatter(
                        self.model.target_values[p][z]['est'], depth,
                        marker='x', s=90, c=self.green)
                if p == 'Bm2' and self.model.priors_from == 'SP':
                    ax.set_xlim([-1, 3])

        for i, param in enumerate(dc_params):
            p = param.name
            ax = dc_axs[i]
            xlabel = param.label
            if param.units:
                xlabel += f' ({param.units})'
            ax.set_xlabel(xlabel, fontsize=12)
            ax.errorbar(1, param.prior, yerr=param.prior_e, fmt='^',
                        ms=9, c=self.blue, elinewidth=1.5, ecolor=self.blue,
                        capsize=6, label='Prior', markeredgewidth=1.5)
            ax.errorbar(3, run.param_results[p]['est'],
                        yerr=run.param_results[p]['err'], fmt='o',
                        c=self.orange, ms=9, elinewidth=1.5, label='Estimate',
                        ecolor=self.orange, capsize=6, markeredgewidth=1.5)
            if self.is_twinX:
                ax.scatter(2, self.model.target_values[p]['est'], marker='x',
                           s=90, c=self.green, label='Target')
            ax.tick_params(bottom=False, labelbottom=False)
            ax.set_xticks(np.arange(5))

        handles, labels = ax11.get_legend_handles_labels()
        handles[-2:] = [h[0] for h in handles[-2:]]
        unique = [(h, l) for i, (h, l) in enumerate(
            zip(handles, labels)) if l not in labels[:i]]
        ax12.legend(*zip(*unique), fontsize=12, loc='center', frameon=False,
                    ncol=1, labelspacing=2, bbox_to_anchor=(0.35, 0.5))
        dv_file = f'out/paramsDV{suffix}'
        dc_file = f'out/paramsDC{suffix}'

        if self.is_twinX:
            dv_file += '_TE.pdf'
            dc_file += '_TE.pdf'
        dv.savefig(f'{dv_file}')
        dc.savefig(f'{dc_file}')
        plt.close(dc)
        plt.close(dv)

    def poc_profiles(self, run, suffix):

        fig, [ax1, ax2] = plt.subplots(1, 2, tight_layout=True)
        fig.subplots_adjust(wspace=0.5)

        ax1.set_xlabel('$P_{S}$ (mmol m$^{-3}$)', fontsize=14)
        ax2.set_xlabel('$P_{L}$ (mmol m$^{-3}$)', fontsize=14)
        ax1.set_ylabel('Depth (m)', fontsize=14)

        ax1.errorbar(
            self.model.POCS.prior['conc'], self.model.POCS.prior['depth'],
            fmt='^', xerr=self.model.POCS.prior['conc_e'], ecolor=self.blue,
            elinewidth=1, c=self.blue, ms=10, capsize=5, fillstyle='full')
        ax1.errorbar(
            run.tracer_results['POCS']['est'], self.model.grid, fmt='o',
            xerr=run.tracer_results['POCS']['err'], ecolor=self.orange,
            elinewidth=1, c=self.orange, ms=8, capsize=5, fillstyle='none',
            zorder=3, markeredgewidth=1)

        ax2.errorbar(
            self.model.POCL.prior['conc'], self.model.POCL.prior['depth'],
            fmt='^', xerr=self.model.POCL.prior['conc_e'], ecolor=self.blue,
            elinewidth=1, c=self.blue, ms=10, capsize=5, fillstyle='full',
            label='Data')
        ax2.errorbar(
            run.tracer_results['POCL']['est'], self.model.grid, fmt='o',
            xerr=run.tracer_results['POCL']['err'], ecolor=self.orange,
            elinewidth=1, c=self.orange, ms=8, capsize=5,
            label='Estimate', fillstyle='none',
            zorder=3, markeredgewidth=1)

        for ax in (ax1, ax2):
            ax.invert_yaxis()
            ax.set_ylim(top=0, bottom=530)
            ax.tick_params(axis='both', which='major', labelsize=12)
        ax2.tick_params(labelleft=False)
        ax.legend(fontsize=12, borderpad=0.2, handletextpad=0.4,
                  loc='lower right')

        filename = f'out/POCprofs{suffix}'
        if self.is_twinX:
            filename += '_TE.pdf'
        fig.savefig(f'{filename}')
        plt.close()

    def residual_pdfs(self, run, suffix):

        state_vars = list(run.x_resids)
        eq_resids = []

        j = 0
        for x in self.model.state_elements:
            if 'R' in x:
                eq_resids.append(state_vars.pop(j))
            else:
                j += 1

        fig, (ax1, ax2) = plt.subplots(1, 2, tight_layout=True)
        ax1.set_ylabel('Probability density', fontsize=16)
        ax1.set_xlabel(r'$\frac{\^x_{i}-x_{o,i}}{\sigma_{o,i}}$',
                       fontsize=14)
        ax2.set_xlabel(r'$\frac{\^{\overline{\varepsilon}h}}'
                       r'{\sigma_{\overline{\varepsilon}h}}$',
                       fontsize=14)

        ax1.hist(state_vars, density=True, color=self.blue)
        ax2.hist(eq_resids, density=True, color=self.blue)

        filename = f'out/pdfs{suffix}'
        if self.is_twinX:
            filename += '_TE'
        fig.savefig(f'{filename}.pdf')
        plt.close()

    def residual_profiles(self, run, suffix):

        fig, [ax1, ax2] = plt.subplots(1, 2, tight_layout=True)
        fig.subplots_adjust(wspace=0.5)
        axs = (ax1, ax2)

        ax1.set_xlabel('$\\varepsilon_{S}$ (mmol m$^{-2}$ d$^{-1}$)',
                       fontsize=14)
        ax2.set_xlabel('$\\varepsilon_{L}$ (mmol m$^{-2}$ d$^{-1}$)',
                       fontsize=14)
        ax1.set_ylabel('Depth (m)', fontsize=14)

        for i, t in enumerate(self.model.tracer_names):
            ax = axs[i]
            if 'POC' in t:
                prior_err = run.gamma*run.P30.prior*self.model.mld
            for zone in self.model.zones:
                depths = zone.depths
                z = zone.label
                ax.scatter(run.integrated_resids[t][z][0], np.mean(depths),
                           marker='o', c=self.orange, s=100, zorder=3, lw=0.7)
                ax.fill_betweenx(
                    depths,
                    (run.integrated_resids[t][z][0]
                     - run.integrated_resids[t][z][1]),
                    (run.integrated_resids[t][z][0]
                     + run.integrated_resids[t][z][1]),
                    color=self.orange, alpha=0.25)
                if self.is_twinX:
                    ax.scatter(
                            self.model.target_values[t][z][0], np.mean(depths),
                            marker='x', c=self.green, s=250)
            ax.axvline(prior_err, ls='--', c=self.blue)
            ax.axvline(-prior_err, ls='--', c=self.blue)
            ax.axvline(0, ls=':', c=self.blue)

        for ax in (ax1, ax2):
            ax.invert_yaxis()
            ax.tick_params(axis='both', which='major', labelsize=12)
        ax2.tick_params(labelleft=False)

        filename = f'out/POCresids{suffix}'
        if self.is_twinX:
            filename += '_TE.pdf'
        fig.savefig(f'{filename}')
        plt.close()

class PlotterModelRuns(PlotterTwinX):
    """Generates all model run result plots.

    Inherits from the PlotterTwinX class. No methods are overridden or
    extended, new methods are simply added. Writes out some numerical results
    for each model run to a single text file (pyrite_out.txt).
    """

    def __init__(self, pickled_model):
        super().__init__(pickled_model)

        self.poc_data()

        priors_str = self.model.priors_from
        dvm_str = 'dvmTrue'

        self.write_output(dvm_str, priors_str)

        for run in self.model.model_runs:

            gamma_str = f'gam{str(run.gamma).replace(".","p")}'
            re_str = f're{str(run.rel_err).replace(".","p")}'
            suffix = f'_{priors_str}_{dvm_str}_{re_str}_{gamma_str}'

            self.sinking_fluxes(run, suffix)
            self.volumetric_fluxes(run, suffix)
            self.budgets(run, suffix)

        for x in ('gamma', 'rel_err'):
            self.param_sensitivity(x, priors_str, dvm_str)
            self.param_relative_errors(x, priors_str, dvm_str)

    def poc_data(self):

        fig, [ax1, ax2] = plt.subplots(1, 2, tight_layout=True)
        fig.subplots_adjust(wspace=0.5)

        ax1.set_xlabel('$P_{S}$ (mmol m$^{-3}$)', fontsize=14)
        ax2.set_xlabel('$P_{L}$ (mmol m$^{-3}$)', fontsize=14)
        ax1.set_ylabel('Depth (m)', fontsize=14)

        ax1.errorbar(
            self.model.POCS.prior['conc'], self.model.POCS.prior['depth'],
            fmt='^', xerr=self.model.POCS.prior['conc_e'], ecolor=self.blue,
            elinewidth=1, c=self.blue, ms=10, capsize=5, fillstyle='full')
        ax1.scatter(
            self.model.data['POC']['POCS'],
            self.model.data['POC']['mod_depth'], c=self.blue, alpha=0.4)

        ax2.errorbar(
            self.model.POCL.prior['conc'], self.model.POCL.prior['depth'],
            fmt='^', xerr=self.model.POCL.prior['conc_e'], ecolor=self.blue,
            elinewidth=1, c=self.blue, ms=10, capsize=5, fillstyle='full')
        ax2.scatter(
            self.model.data['POC']['POCL'],
            self.model.data['POC']['mod_depth'], c=self.blue, alpha=0.4)

        ax1.set_xticks([0, 1, 2, 3])
        ax1.set_xlim([0, 3.4])
        ax2.set_xticks([0, 0.05, 0.1, 0.15])
        ax2.set_xticklabels(['0', '0.05', '0.1', '0.15'])
        ax2.tick_params(labelleft=False)

        for ax in (ax1, ax2):
            ax.invert_yaxis()
            ax.set_ylim(top=0, bottom=530)
            ax.tick_params(axis='both', which='major', labelsize=12)
        ax2.tick_params(labelleft=False)

        fig.savefig('out/data_POC.pdf')
        plt.close()

    def ti_data(self):

        fig, [ax1, ax2] = plt.subplots(1, 2, tight_layout=True)
        fig.subplots_adjust(wspace=0.5)

        ax1.set_xlabel('$Ti_{S}$ (µmol m$^{-3}$)', fontsize=14)
        ax2.set_xlabel('$Ti_{L}$ (µmol m$^{-3}$)', fontsize=14)
        ax1.set_ylabel('Depth (m)', fontsize=14)

        ax1.errorbar(
            self.model.TiS.prior['conc'], self.model.TiS.prior['depth'],
            fmt='^', xerr=self.model.TiS.prior['conc_e'], ecolor=self.blue,
            elinewidth=1, c=self.blue, ms=10, capsize=5, label='LVISF',
            fillstyle='full')

        ax2.errorbar(
            self.model.TiL.prior['conc'], self.model.TiL.prior['depth'],
            fmt='^', xerr=self.model.TiL.prior['conc_e'], ecolor=self.blue,
            elinewidth=1, c=self.blue, ms=10, capsize=5, label='LVISF',
            fillstyle='full')

        ax2.legend(fontsize=12, borderpad=0.2, handletextpad=0.4,
                   loc='lower right')
        ax2.tick_params(labelleft=False)

        for ax in (ax1, ax2):
            ax.invert_yaxis()
            ax.set_ylim(top=0, bottom=530)
            ax.tick_params(axis='both', which='major', labelsize=12)

        fig.savefig('out/ti_data.pdf')
        plt.close()

    def sinking_fluxes(self, run, suffix):

        th_fluxes = pd.read_excel(
            'pyrite_data.xlsx', sheet_name='POC_fluxes_thorium')
        th_depths = th_fluxes['depth']
        th_flux = th_fluxes['flux']
        th_flux_u = th_fluxes['flux_u']
        st_fluxes = pd.read_excel(
            'pyrite_data.xlsx', sheet_name='POC_fluxes_traps')
        st_depths = st_fluxes['depth']
        st_flux = st_fluxes['flux']
        st_flux_u = st_fluxes['flux_u']
        letter_coords = (0.02, 0.93)

        fig, (ax1, ax2) = plt.subplots(1, 2)
        ax1.set_ylabel('Depth (m)', fontsize=14)
        fig.text(
            0.5, 0.03, 'POC flux (mmol m$^{-2}$ d$^{-1}$)',
            fontsize=14, ha='center', va='center')
        for ax in (ax1, ax2):
            ax.invert_yaxis()
            ax.set_ylim(top=0, bottom=530)
            ax.axhline(100, ls=':', c=self.black, zorder=1)

        ax1.errorbar(
            run.flux_profiles['sink_S']['est'],
            np.array(self.model.grid) + 2,
            fmt='o', xerr=run.flux_profiles['sink_S']['err'], ecolor=self.sky,
            c=self.sky, capsize=4, label=self.model.sink_S.label,
            fillstyle='none', elinewidth=1.5, capthick=1.5)

        ax1.errorbar(
            run.flux_profiles['sink_L']['est'],
            np.array(self.model.grid) - 2,
            fmt='o', xerr=run.flux_profiles['sink_L']['err'],
            ecolor=self.vermillion, c=self.vermillion, capsize=4,
            label=self.model.sink_L.label, fillstyle='none', elinewidth=1.5,
            capthick=1.5)

        ax1.legend(loc='lower right', fontsize=12, handletextpad=0.01)
        ax1.annotate(
            'A', xy=letter_coords, xycoords='axes fraction', fontsize=18)

        ax2.tick_params(labelleft=False)
        ax2.errorbar(
            run.flux_profiles['sink_T']['est'], self.model.grid, fmt='o',
            xerr=run.flux_profiles['sink_T']['err'], ecolor=self.orange,
            c=self.orange, capsize=4, zorder=3, label=self.model.sink_T.label,
            fillstyle='none', elinewidth=1.5, capthick=1.5)
        ax2.errorbar(
            th_flux, th_depths + 4, fmt='^', xerr=th_flux_u, ecolor=self.green,
            c=self.green, capsize=4, label='$^{234}$Th-based', elinewidth=1.5,
            capthick=1.5)
        ax2.errorbar(
            st_flux, st_depths - 4, fmt='d', xerr=st_flux_u, ecolor=self.black,
            c=self.black, capsize=4, label='Sed. Traps', elinewidth=1.5,
            capthick=1.5)
        ax2.legend(loc='lower right', fontsize=12, handletextpad=0.01)
        ax2.annotate(
            'B', xy=letter_coords, xycoords='axes fraction', fontsize=18)

        for ax in (ax1, ax2):
            ax.tick_params(axis='both', which='major', labelsize=12)

        fig.savefig(f'out/sinkfluxes{suffix}')
        plt.close()

    def volumetric_fluxes(self, run, suffix):

        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2)
        fig.subplots_adjust(left=0.15, bottom=0.15, wspace=0.1)
        axs = (ax1, ax2, ax3, ax4)
        panels = ('A', 'B', 'C', 'D')
        fig.text(0.5, 0.05, 'POC flux (mmol m$^{-3}$ d$^{-1}$)',
                 fontsize=14, ha='center', va='center')
        fig.text(0.05, 0.5, 'Depth (m)', fontsize=14, ha='center',
                 va='center', rotation='vertical')

        pairs = (('sinkdiv_S', 'sinkdiv_L'), ('remin_S', 'aggregation'),
                 ('remin_L', 'disaggregation'), ('production',))

        for i, pr in enumerate(pairs):
            ax = axs[i]
            if pr[0] != 'production':
                for j, z in enumerate(self.model.zones):
                    depths = z.depths
                    ax.scatter(
                        run.flux_profiles[pr[0]]['est'][j], np.mean(depths),
                        marker='o', c=self.blue, s=14, zorder=3, lw=0.7,
                        label=eval(f'self.model.{pr[0]}.label'))
                    ax.fill_betweenx(
                        depths,
                        (run.flux_profiles[pr[0]]['est'][j]
                         - run.flux_profiles[pr[0]]['err'][j]),
                        (run.flux_profiles[pr[0]]['est'][j]
                         + run.flux_profiles[pr[0]]['err'][j]),
                        color=self.blue, alpha=0.25)
                    ax.scatter(
                        run.flux_profiles[pr[1]]['est'][j], np.mean(depths),
                        marker='o', c=self.orange, s=14, zorder=3, lw=0.7,
                        label=eval(f'self.model.{pr[1]}.label'))
                    ax.fill_betweenx(
                        depths,
                        (run.flux_profiles[pr[1]]['est'][j]
                         - run.flux_profiles[pr[1]]['err'][j]),
                        (run.flux_profiles[pr[1]]['est'][j]
                         + run.flux_profiles[pr[1]]['err'][j]),
                        color=self.orange, alpha=0.25)
                if i == 0:
                    ax.axvline(0, ls=':', c=self.black, zorder=1)

            else:
                depths = self.model.grid
                df = self.model.data['NPP']
                H = self.model.mld
                npp = df.loc[df['target_depth'] >= H]['NPP']
                depth = df.loc[df['target_depth'] >= H]['target_depth']
                ax.scatter(npp/self.model.MOLAR_MASS_C, depth, c=self.orange,
                           alpha=0.5, label='NPP', s=10)
                ax.scatter(
                    run.flux_profiles[pr[0]]['est'], depths, marker='o',
                    c=self.blue, s=14, label=eval(f'self.model.{pr[0]}.label'),
                    zorder=3, lw=0.7)
                ax.errorbar(
                    run.flux_profiles[pr[0]]['est'], depths, fmt='o',
                    xerr=run.flux_profiles[pr[0]]['err'], ecolor=self.blue,
                    elinewidth=0.5, c=self.blue, ms=1.5, capsize=2,
                    label=eval(f'self.model.{pr[0]}.label'), fillstyle='none',
                    markeredgewidth=0.5)

            handles, labels = ax.get_legend_handles_labels()
            unique = [
                (h, l) for i, (h, l) in enumerate(
                    zip(handles, labels)) if l not in labels[:i]]
            ax.legend(*zip(*unique), loc='lower right', fontsize=12,
                      handletextpad=0.01)

            ax.annotate(panels[i], xy=(0.9, 0.8), xycoords='axes fraction',
                        fontsize=12)
            ax.set_yticks([0, 100, 200, 300, 400, 500])
            if i % 2:
                ax.tick_params(labelleft=False)
            ax.invert_yaxis()
            ax.set_ylim(top=0, bottom=520)
        fig.savefig(f'out/fluxes_volumetric{suffix}')
        plt.close()

        fig = plt.figure(tight_layout=True)

        hostL = host_subplot(121, axes_class=AA.Axes, figure=fig)
        parL = hostL.twiny()
        parL.axis['top'].toggle(all=True)
        hostL.set_ylabel('Depth (m)')
        hostL.set_xlabel('Ingestion flux (mmol m$^{-3}$ d$^{-1}$)')
        parL.set_xlabel('$P_S$ remin. flux (mmol m$^{-3}$ d$^{-1}$)')
        hostL.set_xlim(0, 0.3)
        parL.set_xlim(0, 0.3)

        hostR = host_subplot(122, axes_class=AA.Axes, figure=fig)
        hostR.yaxis.set_ticklabels([])
        parR = hostR.twiny()
        parR.axis['top'].toggle(all=True)
        hostR.set_xlabel('Excretion flux (mmol m$^{-3}$ d$^{-1}$)')
        parR.set_xlabel('$P_L$ SFD (mmol m$^{-3}$ d$^{-1}$)')
        hostR.set_xlim(-0.02, 0.02)
        parR.set_xlim(0.02, -0.02)
        hostR.axvline(c=self.black, alpha=0.3)

        for host, par in ((hostL, parL), (hostR, parR)):
            host.axis['right'].toggle(all=False)
            host.axis['left'].major_ticks.set_tick_out('out')
            host.axis['bottom'].label.set_color(self.blue)
            par.axis['top'].label.set_color(self.orange)
            host.axis['left'].label.set_fontsize(14)
            host.axis['bottom'].label.set_fontsize(12)
            host.axis['bottom', 'left'].major_ticklabels.set_size(12)
            par.axis['top'].label.set_fontsize(12)
            par.axis['top'].major_ticklabels.set_size(12)

        for j, z in enumerate(self.model.zones):
            if j < 3:
                host = hostL
                par = parL
                par_flux = 'remin_S'
            else:
                host = hostR
                par = parR
                par_flux = 'sinkdiv_L'
            depths = z.depths
            host.scatter(
                run.flux_profiles['dvm']['est'][j], np.mean(depths),
                marker='o', c=self.blue, s=14, zorder=3, lw=0.7)
            host.fill_betweenx(
                depths,
                (run.flux_profiles['dvm']['est'][j]
                 - run.flux_profiles['dvm']['err'][j]),
                (run.flux_profiles['dvm']['est'][j]
                 + run.flux_profiles['dvm']['err'][j]),
                color=self.blue, alpha=0.25)
            par.scatter(
                run.flux_profiles[par_flux]['est'][j], np.mean(depths),
                marker='o', c=self.orange, s=14, zorder=3, lw=0.7)
            par.fill_betweenx(
                depths,
                (run.flux_profiles[par_flux]['est'][j]
                 - run.flux_profiles[par_flux]['err'][j]),
                (run.flux_profiles[par_flux]['est'][j]
                 + run.flux_profiles[par_flux]['err'][j]),
                color=self.orange, alpha=0.25)
        for ax in (hostL, hostR):
            ax.set_yticks([0, 100, 200, 300, 400, 500])
            ax.invert_yaxis()
            ax.set_ylim(top=0, bottom=510)
            ax.axhline(100, ls=':', c=self.black)

        fig.savefig(f'out/dvmflux{suffix}')
        plt.close()

    def write_output(self, dvm_str, priors_str):

        file = f'out/pyrite_out_{dvm_str}_{priors_str}.txt'
        with open(file, 'w') as f:
            for run in self.model.model_runs:
                print('#################################', file=f)
                print(f'GAMMA = {run.gamma}, RE = {run.rel_err}', file=f)
                print('#################################', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                print('Parameter Estimates', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                for param in run.params:
                    p = param.name
                    if param.dv:
                        for z in self.model.zones:
                            est = run.param_results[p][z.label]['est']
                            err = run.param_results[p][z.label]['err']
                            print(f'{p} ({z.label}): {est:.8f} ± {err:.8f}',
                                  file=f)
                    else:
                        est = run.param_results[p]['est']
                        err = run.param_results[p]['err']
                        print(f'{p}: {est:.3f} ± {err:.3f}', file=f)
                zones_to_print = ['EZ', 'UMZ'] + self.model.zone_names
                print('+++++++++++++++++++++++++++', file=f)
                print('Tracer Inventories', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                for z in zones_to_print:
                    print(f'--------{z}--------', file=f)
                    for t in self.model.tracer_names:
                        est, err = run.inventories[t][z]
                        print(f'{t}: {est:.2f} ± {err:.2f}', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                print('Integrated Fluxes', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                for z in zones_to_print:
                    print(f'--------{z}--------', file=f)
                    for flx in run.flux_integrals.keys():
                        est, err = run.flux_integrals[flx][z]
                        print(f'{flx}: {est:.2f} ± {err:.2f}', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                print('Integrated Residuals', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                for z in zones_to_print:
                    print(f'--------{z}--------', file=f)
                    for t in self.model.tracer_names:
                        est, err = run.integrated_resids[t][z]
                        print(f'{t}: {est:.2f} ± {err:.2f}', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                print('Residence Times', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                for z in zones_to_print:
                    print(f'--------{z}--------', file=f)
                    for t in self.model.tracer_names:
                        est, err = run.res_times[t][z]
                        print(f'{t}: {est:.1f} ± {err:.1f}', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                print('Turnover Timescales', file=f)
                print('+++++++++++++++++++++++++++', file=f)
                for z in zones_to_print:
                    print(f'--------{z}--------', file=f)
                    for t in self.model.tracer_names:
                        print(f'***{t}***', file=f)
                        for flx in run.timescales[t][z]:
                            est, err = run.timescales[t][z][flx]
                            print(f'{flx}: {est:.3f} ± {err:.3f}',
                                  file=f)

    def param_sensitivity(self, sens_variable, priors_str, dvm_str):

        colors = [self.blue, self.green, self.orange, self.radish]
        markers = ['o', '^', 's', 'd']

        for r in self.model.model_runs:
            if sens_variable == 'gamma':
                prefix = '_gam_'
                runs = [r for r in self.model.model_runs if r.rel_err == 0.5]
            else:
                prefix = '_re_'
                runs = [r for r in self.model.model_runs if r.gamma == 0.5]

        for param in runs[0].params:
            p = param.name
            fig, ax = plt.subplots(tight_layout=True)
            ax.axes.yaxis.set_ticks([])
            ax.axes.yaxis.set_ticklabels([])
            ax.axvline(param.prior, c=self.black, ls=':')
            if param.units:
                ax.set_xlabel(f'{param.label} ({param.units})', fontsize=14)
            else:
                ax.set_xlabel(param.label, fontsize=14)
            ax.invert_yaxis()
            if param.dv:
                ax.set_ylabel('Layer', fontsize=14, labelpad=30)
                label_pos = np.arange(
                    1/(len(self.model.zones)*2), 1, 1/len(self.model.zones))
                for i, run in enumerate(runs):
                    x = eval(f'run.{sens_variable}')
                    for j, zone in enumerate(self.model.zones):
                        z = zone.label
                        ax.errorbar(
                            run.param_results[p][z]['est'], 5*j + i, capsize=6,
                            fmt=markers[i], elinewidth=1, c=colors[i],
                            xerr=run.param_results[p][z]['err'],
                            markeredgewidth=1, label=x)
                        if i == 0:
                            ax.annotate(z, xy=(-0.05, 1 - label_pos[j]),
                                        xycoords='axes fraction', fontsize=12)
                            if j < len(self.model.zones) - 1:
                                ax.axhline(5*j + 4, c=self.black, ls='--')
            else:
                for i, run in enumerate(runs):
                    x = eval(f'run.{sens_variable}')
                    z = zone.label
                    ax.errorbar(
                        run.param_results[p]['est'], i, capsize=6,
                        fmt=markers[i], elinewidth=1, c=colors[i],
                        xerr=run.param_results[p]['err'],
                        markeredgewidth=1, label=x)

            handles, labels = ax.get_legend_handles_labels()
            by_label = dict(zip(labels, handles))
            ax.legend(by_label.values(), by_label.keys(), fontsize=12,
                      loc='lower center', bbox_to_anchor=(0.5, 1), ncol=4,
                      frameon=False, labelspacing=1)
            fig.savefig(f'out/senstivity{prefix}{p}_{priors_str}_{dvm_str}')
            plt.close()

    def param_relative_errors(self, sens_variable, priors_str, dvm_str):

        for r in self.model.model_runs:
            if sens_variable == 'gamma':
                prefix = '_gam_'
                runs = [r for r in self.model.model_runs if r.rel_err == 0.5]
                xlabel = '$\\gamma$'
            else:
                prefix = '_re_'
                runs = [r for r in self.model.model_runs if r.gamma == 0.5]
                xlabel = 'Relative errors of remin, sinking, B3 terms'

        tick_labels = [eval(f'str(r.{sens_variable})') for r in runs]
        depthV = [p for p in runs[0].params if p.dv]
        depthC = [p for p in runs[0].params if not p.dv]

        art = {'A': {'c':self.orange, 'm': 'o'},
               'B': {'c':self.blue, 'm': '^'},
               'C': {'c':self.green, 'm': 's'},
               'D': {'c':self.black, 'm': 'd'},
               'E': {'c':self.radish, 'm': 'v'},
               'F': {'c':self.vermillion, 'm': '*'},
               'G': {'c':self.sky, 'm': 'X'},
               'P30': {'c':self.orange, 'm': 'o'},
               'Lp': {'c':self.blue, 'm': '^'},
               'zm': {'c':self.green, 'm': 's'},
               'B3': {'c':self.black, 'm': 'd'},
               'a': {'c':self.radish, 'm': 'v'},}

        fig, axs = plt.subplots(2, 3)
        fig.subplots_adjust(wspace=0.5, hspace=0.1, top=0.85, left=0.15)
        fig.text(0.05, 0.5, 'Relative error', fontsize=14, ha='center',
                  va='center', rotation='vertical')
        fig.text(0.5, 0.02, xlabel, fontsize=14, ha='center', va='center')

        axs_list = fig.get_axes()
        for i, param in enumerate(depthV):
            p = param.name
            ax = axs_list[i]
            ax.annotate(param.label, xy=(0.56, 0.05), xycoords='axes fraction',
                        fontsize=14)
            for zone in self.model.zones:
                z = zone.label
                rel_err = [r.param_results[p][z]['err']
                            / r.param_results[p][z]['est'] for r in runs]
                ax.plot(eval(f'self.model.{sens_variable}s'), rel_err,
                        art[z]['m'], label=z, c=art[z]['c'], fillstyle='none',
                        ls='--')
            if sens_variable == 'gamma':
                ax.set_xscale('log')
                if p == 'Bm2':
                    ax.set_yscale('log')
            ax.set_xticks(eval(f'self.model.{sens_variable}s'))
            ax.get_xaxis().set_major_formatter(ticker.ScalarFormatter())
            if i > 2:
                ax.set_xticklabels(tick_labels)
            else:
                ax.set_xticklabels([])

        leg_elements = [
            Line2D([0], [0], marker=art[z]['m'], ls='none', color=art[z]['c'],
                    label=z, fillstyle='none') for z in self.model.zone_names]
        axs[0][1].legend(handles=leg_elements, loc='center', ncol=7,
                         bbox_to_anchor=(0.4, 1.2), fontsize=12, frameon=False,
                         handletextpad=0.01, columnspacing=1)

        fig.savefig(f'out/relerrs_depthV{prefix}{priors_str}_{dvm_str}')
        plt.close()

        fig, ax = plt.subplots(1, 1, tight_layout=True)
        ax.set_ylabel('Relative error', fontsize=14)
        ax.set_xlabel(xlabel, fontsize=14)

        for i, param in enumerate(depthC):
            p = param.name
            rel_err = [r.param_results[p]['err']
                        / r.param_results[p]['est'] for r in runs]
            ax.plot(eval(f'self.model.{sens_variable}s'), rel_err, art[p]['m'],
                    label=param.label, c=art[p]['c'], fillstyle='none',
                    ls='--')
        if sens_variable == 'gamma':
            ax.set_xscale('log')
        ax.set_xticks(eval(f'self.model.{sens_variable}s'))
        ax.get_xaxis().set_major_formatter(ticker.ScalarFormatter())
        ax.set_xticklabels(tick_labels)

        leg_elements = [
            Line2D([0], [0], marker=art[p.name]['m'], ls='none', label=p.label,
                    color=art[p.name]['c'], fillstyle='none') for p in depthC]
        ax.legend(handles=leg_elements, loc='center', ncol=5,
                    bbox_to_anchor=(0.5, 1.05), fontsize=12, frameon=False,
                    handletextpad=0.01)
        fig.savefig(f'out/relerrs_depthC{prefix}{priors_str}_{dvm_str}')
        plt.close()

    def budgets(self, run, suffix):

        zones = ['EZ', 'UMZ'] + self.model.zone_names
        rfi = run.flux_integrals

        for z in zones:
            fig, (ax1, ax2) = plt.subplots(1, 2, tight_layout=True)
            fig.suptitle(f'{z}')
            ax1.set_ylabel('Integrated flux (mmol m$^{-2}$ d$^{-1}$)',
                           fontsize=14)

            for group in ((ax1, 'S', -1, 1), (ax2, 'L', 1, -1)):
                ax, sf, agg_sign, dagg_sign = group
                ax.axhline(0, c='k', lw=1)
                ax.set_xlabel(f'$P_{sf}$ fluxes', fontsize=14)
                labels = ['SFD', 'Remin.', 'Agg.', 'Disagg.', 'Resid.']
                fluxes = [-rfi[f'sinkdiv_{sf}'][z][0],
                          -rfi[f'remin_{sf}'][z][0],
                          agg_sign*rfi['aggregation'][z][0],
                          dagg_sign*rfi['disaggregation'][z][0],
                          run.integrated_resids[f'POC{sf}'][z][0]]
                flux_errs = [rfi[f'sinkdiv_{sf}'][z][1],
                             rfi[f'remin_{sf}'][z][1],
                             rfi['aggregation'][z][1],
                             rfi['disaggregation'][z][1],
                             run.integrated_resids[f'POC{sf}'][z][1]]
                if sf == 'S':
                    labels.insert(-1, 'Prod.')
                    fluxes.insert(-1, rfi['production'][z][0])
                    flux_errs.insert(-1, rfi['production'][z][1])
                    if z in ('EZ', 'A', 'B', 'C'):
                        labels.insert(-1, 'DVM')
                        fluxes.insert(-1, -rfi['dvm'][z][0])
                        flux_errs.insert(-1, rfi['dvm'][z][1])
                if sf == 'L' and z in ('UMZ', 'D', 'E', 'F', 'G'):
                    labels.insert(-1, 'DVM')
                    fluxes.insert(-1, rfi['dvm'][z][0])
                    flux_errs.insert(-1, rfi['dvm'][z][1])

                ax.bar(list(range(len(fluxes))), fluxes, yerr=flux_errs,
                       tick_label=labels, color=self.blue)
                for tick in ax.get_xticklabels():
                    tick.set_rotation(45)

            fig.savefig(f'out/budget{z}{suffix}.pdf')
            plt.close()

class PlotterTwoModel():
    """Makes plots with results from two models."""

    def __init__(self, na_model, sp_model):

        with open(na_model, 'rb') as file:
            self.na_model = pickle.load(file)

        with open(sp_model, 'rb') as file:
            self.sp_model = pickle.load(file)

        self.define_colors()

        args = (0.5, 0.5)

        self.compare_params(*args)
        self.budgets_4panel(*args)
        self.sensitivity_4panel()
        self.poc_profiles(*args)
        self.paramsDC_2model(*args)
        self.paramsDV_2model(*args)
        self.residual_profiles_2model(*args)
        self.sinking_fluxes_2model(*args)
        self.volumetric_fluxes_2model(*args)
        self.dvm_fluxes_2model(*args)

    def define_colors(self):

        self.black = '#000000'
        self.orange = '#E69F00'
        self.sky = '#56B4E9'
        self.green = '#009E73'
        self.blue = '#0072B2'
        self.vermillion = '#D55E00'
        self.radish = '#CC79A7'
        self.white = '#FFFFFF'

    def compare_params(self, gamma, rel_err):

        dpy = self.na_model.DAYS_PER_YEAR

        data = {
                'MNA': {'B2': (2/dpy, 0.2/dpy),
                        'Bm2': (156/dpy, 17/dpy),
                        'Bm1s': (13/dpy, 1/dpy)},
                'MNWA': {0: {'depth': 25.5, 'thick':50.9,
                             'Bm1s': (70/dpy, 137/dpy),
                             'B2': (9/dpy, 24/dpy),
                             'Bm2': (2690/dpy, 10000/dpy)},
                         1: {'depth': 85.1, 'thick':68.4,
                             'Bm1s': (798/dpy, 7940/dpy),
                             'B2': (11/dpy, 30/dpy),
                             'Bm2': (2280/dpy, 10000/dpy)},
                         2: {'depth': 169.5, 'thick':100.4,
                             'Bm1s': (378/dpy, 3520/dpy),
                             'B2': (13/dpy, 50/dpy),
                             'Bm2': (1880/dpy, 10000/dpy)},
                         3: {'depth': 295.3, 'thick':151.1,
                             'Bm1s': (1766/dpy, 10000000/dpy),
                             'B2': (18/dpy, 89/dpy),
                             'Bm2': (950/dpy, 5700/dpy)},
                         4: {'depth': 482.8, 'thick':224,
                             'Bm1s': (113/dpy, 10000/dpy),
                             'B2': (17/dpy, 77/dpy),
                             'Bm2': (870/dpy, 5000/dpy)}},
                'BRIG': {'depth': np.arange(250, 555, 50),
                         'Bm2': (0.27*np.exp(-0.0024*np.arange(250, 555, 50)),
                                  0.03*np.exp(-0.00027*np.arange(250, 555, 50))
                                  )},
                'CLEG': {'Bm1s': {'EP': {'depth': [8.694176691, 29.48109721,
                                                   49.6013742, 70.31883177,
                                                   88.87067924, 124.4489829,
                                                   173.9252911, 248.4389759,
                                                   348.4530184, 448.5886014],
                                         'est': [0.051887519, 0.178345733,
                                                 0.22605604, 0.071119386,
                                                 0.033423448, 0.022647476,
                                                 0.020748539, 0.005321444,
                                                 0.0040375, 0.003877201],
                                         'err': [0.029125986, 0.068928149,
                                                 0.08115125, 0.08557525,
                                                 0.091710495, 0.033536243,
                                                 0.033705286, 0.017032243,
                                                 0.01124989, 0.010966607]},
                                  'SP': {'depth': [55.39594182, 105.7416053,
                                                   170.3402765, 281.4015083,
                                                   372.3626324],
                                         'est': [0.838243981, 0.832458749,
                                                 0.467690663, 0.374724094,
                                                 0.293289586]}},
                         'B2': {'EP': {'depth': [30.77953839, 50.76269642,
                                                 80.61454904, 102.5688552,
                                                 142.430575, 176.0573893,
                                                 251.5227028, 350.735996,
                                                 450.5050505],
                                       'est': [0.035755288, 0.109873452,
                                               0.010680515, 0.003620261,
                                               0.038039483, 0.003907699,
                                               0.000413925, 0.000350797,
                                               0.000667077],
                                       'err': [0.012214804, 0.032958757,
                                               0.017898566, 0.003106243,
                                               0.045793402, 0.003063559,
                                               0.001785665, 0.001306329,
                                               0.002227189]},
                                'SP': {'depth': [20.64375691, 62.80198408,
                                                 139.5674981, 251.4684366,
                                                 357.0709774],
                                       'est': [0.086868158, 0.047762631,
                                               0.021107875, 0.00532128,
                                               0.00471852]}},
                         'Bm2': {'EP': {'depth': [22.95595846, 38.2227626,
                                                  62.75455011, 83.91835297,
                                                  119.0743645, 168.1556833,
                                                  246.9700358, 345.6563059,
                                                  447.1153846],
                                        'est': [2.440824069, 11.45628156,
                                                1.859098607, 0.533865192,
                                                0.664345743, 1.854904373,
                                                0.159348769, 0.175967215,
                                                0.212286842],
                                        'err': [3.701200346, 8.456005002,
                                                6.517677793, 3.242497695,
                                                1.312408875, 2.910707038,
                                                0.714475243, 0.50814118,
                                                1.008215058]}}}}

        fig, (na_axs, sp_axs) = plt.subplots(2, 3, figsize=(7, 6))
        fig.subplots_adjust(bottom=0.12, top=0.85, hspace=0.1)
        capsize = 4
        fig.text(0.05, 0.5, 'Depth (m)', fontsize=14, ha='center',
                 va='center', rotation='vertical')

        for model in (self.na_model, self.sp_model):
            for r in model.model_runs:
                if r.gamma == gamma and r.rel_err == rel_err:
                    run = r
                    break
            if model == self.na_model:
                axs = na_axs
                ylabel = 'NA inversion'
            else:
                axs = sp_axs
                ylabel = 'SP inversion'
            B2 = {}
            for z in model.zone_names:
                B2p, Psi = sym.symbols(f'B2p_{z} POCS_{z}')
                if z == 'A':
                    Psa = Psi
                else:
                    Psim1 = sym.symbols(
                        f'POCS_{self.na_model.previous_zone(z)}')
                    Psa = (Psi + Psim1)/2
                y = B2p*Psa
                B2[z] = model.eval_symbolic_func(run, y)

            for i, ax in enumerate(axs):
                if i == 0:
                    p = 'Bm1s'
                    label = f'{run.Bm1s.label} ({run.Bm1s.units})'
                elif i == 1:
                    p = 'Bm2'
                    label = f'{run.Bm2.label} ({run.Bm2.units})'
                    ax.tick_params(labelleft=False)
                else:
                    p = 'B2'
                    label = '$\\beta_2$ (d$^{-1}$)'
                    ax.tick_params(labelleft=False)
                    ax.set_ylabel(ylabel, fontsize=14, rotation=270,
                                  labelpad=20)
                    ax.yaxis.set_label_position('right')
                if ax in na_axs:
                    ax.tick_params(labelbottom=False)
                else:
                    ax.set_xlabel(label, fontsize=14)
                ax.invert_yaxis()
                ax.set_xscale('log')
                ax.set_ylim([600, -50])

                for zone in model.zones:
                    z = zone.label
                    d_av = np.mean(zone.depths)
                    d_err = zone.thick/2
                    if p == 'B2':
                        data_point = B2[z][0]
                        data_err = B2[z][1]
                    else:
                        data_point = run.param_results[p][z]['est']
                        data_err = run.param_results[p][z]['err']
                    ax.errorbar(data_point, d_av, fmt='o', yerr=d_err,
                                c=self.vermillion, capsize=capsize, zorder=9)
                    ax.scatter(data_err, d_av, marker='o', facecolors='none',
                               edgecolors=self.black, zorder=10)
                for z in data['MNWA']:
                    d_av = data['MNWA'][z]['depth']
                    d_err = data['MNWA'][z]['thick']/2
                    ax.errorbar(data['MNWA'][z][p][0], d_av, fmt='s',
                                yerr=d_err, c=self.radish, capsize=4)
                    ax.scatter(data['MNWA'][z][p][1], d_av, marker='s',
                               facecolors='none', edgecolors=self.black,
                               zorder=10)
                ax.errorbar(data['MNA'][p][0], 225, fmt='d', yerr=75,
                            c=self.green, capsize=capsize, zorder=4)
                ax.scatter(data['MNA'][p][1], 225, marker='d', zorder=10,
                           edgecolors=self.black, facecolors='none')
                if p == 'Bm2':
                    ax.scatter(data['BRIG'][p][0], data['BRIG']['depth'],
                               marker='*', c=self.orange, s=60)
                    ax.scatter(data['BRIG'][p][1], data['BRIG']['depth'],
                               marker='*', zorder=10, edgecolors=self.black,
                               facecolors='none', s=60)
                for s in data['CLEG'][p]:
                    if s == 'EP':
                        m = '^'
                        c = self.sky
                    else:
                        m = 'v'
                        c = self.blue
                    ax.scatter(data['CLEG'][p][s]['est'],
                               data['CLEG'][p][s]['depth'], marker=m, c=c)
                    if 'err' in data['CLEG'][p][s].keys():
                        ax.scatter(data['CLEG'][p][s]['err'],
                                   data['CLEG'][p][s]['depth'],
                                   marker=m, zorder=10, edgecolors=self.black,
                                   facecolors='none')

            axs[0].set_xlim([0.001, 100000])
            axs[0].set_xticks([0.001, 0.1, 10, 1000, 10**5])
            axs[1].set_xlim([0.01, 100])
            axs[1].set_xticks([0.01, 0.1, 1, 10, 100])
            axs[2].set_xlim([0.00001, 1])
            axs[2].set_xticks([0.00001, 0.0001, 0.001, 0.01, 0.1, 1])
            axs[2].yaxis.set_label_position('right')

        leg_elements = [
            Line2D([0], [0], marker='o', mec=self.black, c=self.white,
                    label='This study \nStation P',
                    markerfacecolor=self.vermillion, ms=9),
            Line2D([0], [0], marker='s', mec=self.black, c=self.white,
                    label='Murnane et al. (1994)\nNWAO',
                    markerfacecolor=self.radish, ms=9),
            Line2D([0], [0], marker='d', mec=self.black, c=self.white,
                    label='Murnane et al. (1996)\nNABE',
                    markerfacecolor=self.green, ms=9),
            Line2D([0], [0], marker='*', mec=self.black, c=self.white,
                    label='Briggs et al. (2020)\nSNAO, SO',
                    markerfacecolor=self.orange, ms=12),
            Line2D([0], [0], marker='^', mec=self.black, c=self.white,
                    label='Clegg et al. (1991)\nEPO',
                    markerfacecolor=self.sky, ms=9),
            Line2D([0], [0], marker='v', mec=self.black, c=self.white,
                    label='Clegg et al. (1991)\nStation P',
                    markerfacecolor=self.blue, ms=9)]
        na_axs[1].legend(handles=leg_elements, fontsize=10, ncol=3,
                    bbox_to_anchor=(0.44, 1.02), loc='lower center',
                    handletextpad=0.01, frameon=False)

        fig.savefig('out/compareparams.pdf')
        plt.close()

    def budgets_4panel(self, gamma, rel_err):

        for z in ('EZ', 'UMZ'):

            fig, (na_axs, sp_axs) = plt.subplots(2, 2)
            fig.subplots_adjust(hspace=0.02, wspace=0.1, bottom=0.2, top=0.9)
            sp_axs[0].set_ylabel('Integrated flux (mmol m$^{-2}$ d$^{-1}$)',
                                  fontsize=14)
            sp_axs[0].yaxis.set_label_coords(-0.2, 1)

            for model in (self.na_model, self.sp_model):
                for r in model.model_runs:
                    if r.gamma == gamma and r.rel_err == rel_err:
                        run = r
                        break
                if model == self.na_model:
                    axs = na_axs
                    ylabel = 'NA inversion'
                    [ax.axes.xaxis.set_visible(False) for ax in axs]
                else:
                    axs = sp_axs
                    ylabel = 'SP inversion'

                rfi = run.flux_integrals

                ax1, ax2 = axs
                ax2.set_ylabel(ylabel, fontsize=14, rotation=270, labelpad=20)
                ax2.yaxis.set_label_position('right')

                for group in ((ax1, 'S', -1, 1), (ax2, 'L', 1, -1)):
                    ax, sf, agg_sign, dagg_sign = group
                    ax.axhline(0, c='k', lw=1)
                    ax.set_ylim([-16, 15])
                    ax.set_xlabel(f'$P_{sf}$ fluxes', fontsize=14)
                    labels = ['SFD', 'Remin.', 'Agg.', 'Disagg.', 'Resid.']
                    fluxes = [-rfi[f'sinkdiv_{sf}'][z][0],
                              -rfi[f'remin_{sf}'][z][0],
                              agg_sign*rfi['aggregation'][z][0],
                              dagg_sign*rfi['disaggregation'][z][0],
                              run.integrated_resids[f'POC{sf}'][z][0]]
                    flux_errs = [rfi[f'sinkdiv_{sf}'][z][1],
                                 rfi[f'remin_{sf}'][z][1],
                                 rfi['aggregation'][z][1],
                                 rfi['disaggregation'][z][1],
                                 run.integrated_resids[f'POC{sf}'][z][1]]
                    if sf == 'S':
                        labels.insert(-1, 'Prod.')
                        fluxes.insert(-1, rfi['production'][z][0])
                        flux_errs.insert(-1, rfi['production'][z][1])
                    else:
                        ax.tick_params(labelleft=False)

                    if sf == 'S' and z in ('EZ', 'A', 'B', 'C'):
                        labels.insert(-1, 'DVM')
                        fluxes.insert(-1, -rfi['dvm'][z][0])
                        flux_errs.insert(-1, rfi['dvm'][z][1])
                    elif sf == 'L' and z in ('UMZ', 'D', 'E', 'F', 'G'):
                        labels.insert(-1, 'DVM')
                        fluxes.insert(-1, rfi['dvm'][z][0])
                        flux_errs.insert(-1, rfi['dvm'][z][1])

                    ax.bar(list(range(len(fluxes))), fluxes, yerr=flux_errs,
                           tick_label=labels, color=self.blue)
                    for tick in ax.get_xticklabels():
                        tick.set_rotation(45)

            fig.savefig(f'out/budgets_4panel_{z}.pdf')
            plt.close()

    def sensitivity_4panel(self):

        def eval_symbolic_func2(model, run, y):

            x_symbolic = list(y.free_symbols)
            x_numerical = []
            x_indices = []
            for x in x_symbolic:
                idx = model.state_elements.index(x.name)
                x_indices.append(idx)
                x_numerical.append(model.xo[idx])

            result = sym.lambdify(x_symbolic, y)(*x_numerical)

            variance_sym = 0
            derivs = [y.diff(x) for x in x_symbolic]
            cvm = run.Co[np.ix_(x_indices, x_indices)]
            for i, row in enumerate(cvm):
                for j, _ in enumerate(row):
                    if i == j:
                        variance_sym += (derivs[i]**2)*cvm[i, j]
            variance = sym.lambdify(x_symbolic, variance_sym)(*x_numerical)
            error = np.sqrt(variance)

            return result, error

        prior_fluxes_sym = self.na_model.calculate_fluxes()
        _, prior_fluxes_sym_integrated = self.na_model.integrate_fluxes(
            prior_fluxes_sym)

        prior_fluxes = {}
        for i, model in enumerate((self.na_model, self.sp_model)):
            m = model.priors_from
            prior_fluxes[m] = {}
            for r in model.model_runs:
                if r.gamma == 0.5 and r.rel_err == 0.5:
                    run = r
            for f in prior_fluxes_sym_integrated.keys():
                prior_fluxes[m][f] = {}
                for z in ('EZ', 'UMZ'):
                    prior_fluxes[m][f][z] = eval_symbolic_func2(
                        model, run, prior_fluxes_sym_integrated[f][z])


        width = 0.2
        combos = ((0.5, 0.5), (0.5, 1), (1, 0.5), (1, 1))
        run_colors = {0: self.blue, 1: self.orange, 2: self.green,
                      3: self.vermillion, 4: self.radish}

        for z in ('EZ', 'UMZ'):

            fig, (na_axs, sp_axs) = plt.subplots(2, 2)
            fig.subplots_adjust(hspace=0.05, bottom=0.2, top=0.9)
            sp_axs[0].set_ylabel('Integrated flux (mmol m$^{-2}$ d$^{-1}$)',
                                  fontsize=14)
            sp_axs[0].yaxis.set_label_coords(-0.2, 1)

            for model in (self.na_model, self.sp_model):
                runs = []
                for (gamma, rel_err) in combos:
                    for r in model.model_runs:
                        if r.gamma == gamma and r.rel_err == rel_err:
                            runs.append(r)
                if model == self.na_model:
                    axs = na_axs
                    [ax.axes.xaxis.set_visible(False) for ax in axs]
                else:
                    axs = sp_axs
                    [ax.set_ylim([-20, 20]) for ax in axs]

                runs.insert(0, prior_fluxes[model.priors_from])

                ax1, ax2 = axs
                ylabel = f'{model.priors_from} inversions'
                ax2.set_ylabel(ylabel, fontsize=14, rotation=270, labelpad=20)
                ax2.yaxis.set_label_position('right')

                for i, run in enumerate(runs):
                    if i > 0:
                        rfi = {**run.flux_integrals, **run.integrated_resids}
                    else:
                        rfi = run
                    color = run_colors[i]
                    for group in ((ax1, 'S', -1, 1), (ax2, 'L', 1, -1)):
                        ax, sf, agg_sign, dagg_sign = group
                        ax.axhline(0, c='k', lw=0.5)
                        ax.set_xlabel(f'$P_{sf}$ fluxes', fontsize=14)
                        ax_labels = [
                            'SFD', 'Remin.', 'Agg.', 'Disagg.', 'Resid.']
                        fluxes = [-rfi[f'sinkdiv_{sf}'][z][0],
                                  -rfi[f'remin_{sf}'][z][0],
                                  agg_sign*rfi['aggregation'][z][0],
                                  dagg_sign*rfi['disaggregation'][z][0]]
                        flux_errs = [rfi[f'sinkdiv_{sf}'][z][1],
                                     rfi[f'remin_{sf}'][z][1],
                                     rfi['aggregation'][z][1],
                                     rfi['disaggregation'][z][1]]
                        if i > 0:
                            fluxes.append(rfi[f'POC{sf}'][z][0])
                            flux_errs.append(rfi[f'POC{sf}'][z][1])
                        else:
                            fluxes.append(0)
                            flux_errs.append(
                                runs[1].gamma*runs[1].P30.prior*model.mld)
                        if sf == 'S':
                            ax_labels.insert(-1, 'Prod.')
                            fluxes.insert(-1, rfi['production'][z][0])
                            flux_errs.insert(-1, rfi['production'][z][1])
                            if z == 'EZ':
                                ax_labels.insert(-1, 'DVM')
                                fluxes.insert(-1, -rfi['dvm'][z][0])
                                flux_errs.insert(-1, rfi['dvm'][z][1])
                        elif sf == 'L' and z == 'UMZ':
                            ax_labels.insert(-1, 'DVM')
                            fluxes.insert(-1, rfi['dvm'][z][0])
                            flux_errs.insert(-1, rfi['dvm'][z][1])
                        x = np.arange(len(ax_labels))
                        if i == 0:
                            positions = x - width*2
                        elif i == 1:
                            positions = x - width
                        elif i == 2:
                            positions = x
                        elif i == 3:
                            positions = x + width
                        else:
                            positions = x + width*2
                        ax.bar(positions, fluxes, width=width, yerr=flux_errs,
                               tick_label=ax_labels, color=color,
                               error_kw={'elinewidth': 1})
                        ax.set_xticks(x)
                        ax.set_xticklabels(ax_labels)
                        for tick in ax.get_xticklabels():
                            tick.set_rotation(45)
            leg_elements = [
                Line2D([0], [0], c=self.blue, marker='s', ls='none', ms=6,
                       label='prior ($\gamma = 0.5, RE = 0.5$)'),
                Line2D([0], [0], c=self.orange, marker='s', ls='none', ms=6,
                       label='$\gamma = 0.5, RE = 0.5$'),
                Line2D([0], [0], c=self.green, marker='s', ls='none', ms=6,
                       label='$\gamma = 0.5, RE = 1$'),
                Line2D([0], [0], c=self.vermillion, marker='s', ls='none', ms=6,
                       label='$\gamma = 1, RE = 0.5$'),
                Line2D([0], [0], c=self.radish, marker='s', ls='none', ms=6,
                       label='$\gamma = 1, RE = 1$')]
            na_axs[1].legend(handles=leg_elements, fontsize=9,
                               frameon=False, handletextpad=-0.5,
                               loc=(-0.04,0.54), labelspacing=0)

            fig.savefig(f'out/sensitivity_4panel_{z}.pdf')
            plt.close()

    def poc_profiles(self, gamma, rel_err):

        fig, [ax1, ax2] = plt.subplots(1, 2, tight_layout=True)
        fig.subplots_adjust(wspace=0.5)

        ax1.set_xlabel('$P_{S}$ (mmol m$^{-3}$)', fontsize=14)
        ax2.set_xlabel('$P_{L}$ (mmol m$^{-3}$)', fontsize=14)
        ax1.set_ylabel('Depth (m)', fontsize=14)

        for r in self.na_model.model_runs:
            if r.gamma == gamma and r.rel_err == rel_err:
                nrun = r
                break

        for r in self.sp_model.model_runs:
            if r.gamma == gamma and r.rel_err == rel_err:
                srun = r
                break

        ngrid = [d - 5 for d in self.sp_model.grid]
        sgrid = [d + 5 for d in self.sp_model.grid]

        ax1.errorbar(
            self.sp_model.POCS.prior['conc'],
            self.sp_model.POCS.prior['depth'],
            fmt='^', xerr=self.sp_model.POCS.prior['conc_e'], ecolor=self.blue,
            elinewidth=1, c=self.blue, ms=10, capsize=5, fillstyle='full')
        ax1.errorbar(
            nrun.tracer_results['POCS']['est'], ngrid, fmt='o',
            xerr=nrun.tracer_results['POCS']['err'], ecolor=self.orange,
            elinewidth=1, c=self.orange, ms=8, capsize=5, fillstyle='none',
            zorder=3, markeredgewidth=1)
        ax1.errorbar(
            srun.tracer_results['POCS']['est'], sgrid, fmt='s',
            xerr=srun.tracer_results['POCS']['err'], ecolor=self.green,
            elinewidth=1, c=self.green, ms=8, capsize=5,
            label='Data', fillstyle='none',
            zorder=3, markeredgewidth=1)

        ax2.errorbar(
            self.sp_model.POCL.prior['conc'],
            self.sp_model.POCL.prior['depth'],
            fmt='^', xerr=self.sp_model.POCL.prior['conc_e'], ecolor=self.blue,
            elinewidth=1, c=self.blue, ms=10, capsize=5, fillstyle='full',
            label='Data')
        ax2.errorbar(
            nrun.tracer_results['POCL']['est'], ngrid, fmt='o',
            xerr=nrun.tracer_results['POCL']['err'], ecolor=self.orange,
            elinewidth=1, c=self.orange, ms=8, capsize=5,
            label='NA', fillstyle='none',
            zorder=3, markeredgewidth=1)
        ax2.errorbar(
            srun.tracer_results['POCL']['est'], sgrid, fmt='s',
            xerr=srun.tracer_results['POCL']['err'], ecolor=self.green,
            elinewidth=1, c=self.green, ms=8, capsize=5,
            label='SP', fillstyle='none',
            zorder=3, markeredgewidth=1)

        ax1.set_xticks([0, 1, 2, 3])
        ax1.set_xlim([0, 3.4])
        ax2.set_xticks([0, 0.05, 0.1, 0.15])
        ax2.set_xticklabels(['0', '0.05', '0.1', '0.15'])
        ax2.tick_params(labelleft=False)

        for ax in (ax1, ax2):
            ax.invert_yaxis()
            ax.set_ylim(top=0, bottom=530)
            ax.tick_params(axis='both', which='major', labelsize=12)
        ax2.tick_params(labelleft=False)
        ax.legend(fontsize=12, borderpad=0.2, handletextpad=0.4,
                  loc='lower right')

        fig.savefig('out/POCprofs_2model.pdf')
        plt.close()

    def paramsDC_2model(self, gamma, rel_err):

        for r in self.na_model.model_runs:
            if r.gamma == gamma and r.rel_err == rel_err:
                nrun = r
                break

        for r in self.sp_model.model_runs:
            if r.gamma == gamma and r.rel_err == rel_err:
                srun = r
                break

        fig, ((ax1, ax2, ax3), (ax4, ax5, ax6)) = plt.subplots(
            2, 3, tight_layout=True)
        dc_axs = ax1, ax2, ax3, ax4, ax5
        ax6.axis('off')

        dc_params = [p for p in nrun.params if not p.dv]

        for i, param in enumerate(dc_params):
            p = param.name
            ax = dc_axs[i]
            xlabel = param.label
            if param.units:
                xlabel += f' ({param.units})'
            ax.set_xlabel(xlabel, fontsize=12)
            ax.errorbar(1, param.prior, yerr=param.prior_e, fmt='^',
                        c=self.blue, elinewidth=1.5, ecolor=self.blue, ms=9,
                        capsize=6, label='Prior', markeredgewidth=1.5)
            ax.errorbar(2, nrun.param_results[p]['est'], fmt='o',
                        yerr=nrun.param_results[p]['err'], c=self.orange, ms=9,
                        ecolor=self.orange, elinewidth=1.5, capsize=6,
                        label='Estimate (NA)', markeredgewidth=1.5)
            ax.errorbar(3, srun.param_results[p]['est'], fmt='s',
                        yerr=srun.param_results[p]['err'], c=self.green, ms=9,
                        ecolor=self.green, elinewidth=1.5, capsize=6,
                        label='Estimate (SP)', markeredgewidth=1.5)
            ax.tick_params(bottom=False, labelbottom=False)
            ax.set_xticks(np.arange(5))

        handles, labels = ax5.get_legend_handles_labels()
        handles = [h[0] for h in handles]
        unique = [(h, l) for i, (h, l) in enumerate(
            zip(handles, labels)) if l not in labels[:i]]
        ax6.legend(*zip(*unique), fontsize=12, loc='center', frameon=False,
                    ncol=1, labelspacing=2, bbox_to_anchor=(0.35, 0.5))

        fig.savefig('out/paramsDC_2model.pdf')
        plt.close()


    def paramsDV_2model(self, gamma, rel_err):

        fig, (na_axs, sp_axs) = plt.subplots(2, 4, figsize=(6.5,4))
        fig.subplots_adjust(left=0.14, right=0.95, top=0.95, bottom=0.15)
        fig.text(0.05, 0.5, 'Depth (m)', fontsize=14, ha='center',
                 va='center', rotation='vertical')

        xlims = {'ws': (0.5, 3.2), 'wl': (9, 31),
                 'Bm1s': (0, 0.16), 'Bm2':(-1, 3)}


        for model in (self.na_model, self.sp_model):
            for r in model.model_runs:
                if r.gamma == gamma and r.rel_err == rel_err:
                    run = r
                    break
            if model == self.na_model:
                axs = na_axs
                ylabel = 'NA inversion'
            else:
                axs = sp_axs
                ylabel = 'SP inversion'
            dv_params = [p for p in run.params if p.dv and p.name in xlims]
            for i, param in enumerate(dv_params):
                p = param.name
                if p not in xlims.keys():
                    continue
                ax = axs[i]
                if i:
                    ax.tick_params(labelleft=False)
                if i == 3:
                    ax.set_ylabel(ylabel, fontsize=14, rotation=270,
                                  labelpad=20)
                    ax.yaxis.set_label_position('right')
                if ylabel == 'SP inversion':
                    ax.set_xlabel(f'{param.label} ({param.units})',
                                  fontsize=12)
                else:
                    ax.axes.xaxis.set_ticklabels([])
                ax.invert_yaxis()
                ax.set_xlim(xlims[p])
                ax.set_ylim(top=0, bottom=530)
                ax.tick_params(axis='both', which='major', labelsize=12)
                ax.axvline(param.prior, c=self.blue, lw=1.5, ls=':')
                ax.axvline(param.prior - param.prior_e, c=self.blue, lw=1.5,
                           ls='--')
                ax.axvline(param.prior + param.prior_e, c=self.blue, lw=1.5,
                           ls='--')
                for j, z in enumerate(self.na_model.zone_names):
                    zone = self.na_model.zones[j]
                    if 'w' in p:
                        depth = zone.depths[1]
                        ax.errorbar(
                            run.param_results[p][z]['est'], depth, fmt='o',
                            xerr=run.param_results[p][z]['err'], ms=8,
                            ecolor=self.orange, elinewidth=1, c=self.orange,
                            capsize=6, fillstyle='none', zorder=3,
                            markeredgewidth=1)
                    else:
                        depths = zone.depths
                        depth = np.mean(depths)
                        ax.scatter(
                            run.param_results[p][z]['est'], depth, marker='o',
                            c=self.orange, s=14, zorder=3)
                        ax.fill_betweenx(
                            depths,
                            (run.param_results[p][z]['est']
                              - run.param_results[p][z]['err']),
                            (run.param_results[p][z]['est']
                              + run.param_results[p][z]['err']),
                            color=self.orange, alpha=0.25)

        fig.savefig('out/paramsDV_2model.pdf')
        plt.close()

    def residual_profiles_2model(self, gamma, rel_err):

        fig, (na_axs, sp_axs) = plt.subplots(2, 2)
        fig.subplots_adjust(left=0.14, right=0.92, top=0.95, bottom=0.15,
                            wspace=0.1, hspace=0.1)
        fig.text(0.05, 0.5, 'Depth (m)', fontsize=14, ha='center',
                 va='center', rotation='vertical')

        for model in (self.na_model, self.sp_model):
            for r in model.model_runs:
                if r.gamma == gamma and r.rel_err == rel_err:
                    run = r
                    break
            if model == self.na_model:
                axs = na_axs
                ylabel = 'NA inversion'
                [ax.axes.xaxis.set_ticklabels([]) for ax in axs]
            else:
                axs = sp_axs
                ylabel = 'SP inversion'
                axs[0].set_xlabel(
                    '$\\overline{\\varepsilon_{S}}h$ (mmol m$^{-2}$ d$^{-1}$)',
                    fontsize=14)
                axs[1].set_xlabel(
                    '$\\overline{\\varepsilon_{L}}h$ (mmol m$^{-2}$ d$^{-1}$)',
                    fontsize=14)
            for i, t in enumerate(model.tracer_names):
                ax = axs[i]
                ax.invert_yaxis()
                ax.set_xlim([-4, 6])
                ax.tick_params(axis='both', which='major', labelsize=12)
                ax.set_yticks([0, 100, 200, 300, 400, 500])
                if i == 1:
                    ax.set_ylabel(ylabel, fontsize=14, rotation=270,
                                  labelpad=20)
                    ax.yaxis.set_label_position('right')
                if 'POC' in t:
                    prior_err = run.gamma*run.P30.prior*model.mld
                for zone in model.zones:
                    depths = zone.depths
                    z = zone.label
                    ax.scatter(run.integrated_resids[t][z][0], np.mean(depths),
                               marker='o', c=self.orange, s=100, zorder=3,
                               lw=0.7)
                    ax.fill_betweenx(
                        depths,
                        (run.integrated_resids[t][z][0]
                         - run.integrated_resids[t][z][1]),
                        (run.integrated_resids[t][z][0]
                         + run.integrated_resids[t][z][1]),
                        color=self.orange, alpha=0.25)
                ax.axvline(prior_err, ls='--', c=self.blue)
                ax.axvline(-prior_err, ls='--', c=self.blue)
                ax.axvline(0, ls=':', c=self.blue)
                axs[1].tick_params(labelleft=False)

        fig.savefig('out/POCresids_2model.pdf')
        plt.close()

    def sinking_fluxes_2model(self, gamma, rel_err):

        th_fluxes = pd.read_excel(
            'pyrite_data.xlsx', sheet_name='POC_fluxes_thorium')
        th_depths = th_fluxes['depth']
        th_flux = th_fluxes['flux']
        th_flux_u = th_fluxes['flux_u']
        st_fluxes = pd.read_excel(
            'pyrite_data.xlsx', sheet_name='POC_fluxes_traps')
        st_depths = st_fluxes['depth']
        st_flux = st_fluxes['flux']
        st_flux_u = st_fluxes['flux_u']

        fig, (na_axs, sp_axs) = plt.subplots(2, 2, figsize=(6, 6))
        fig.subplots_adjust(left=0.16, right=0.92, top=0.95, bottom=0.11,
                            wspace=0.15, hspace=0.1)
        fig.text(0.05, 0.5, 'Depth (m)', fontsize=14, ha='center',
                 va='center', rotation='vertical')
        fig.text(0.54, 0.03, 'POC flux (mmol m$^{-2}$ d$^{-1}$)',
                 fontsize=14, ha='center', va='center')

        for model in (self.na_model, self.sp_model):
            for r in model.model_runs:
                if r.gamma == gamma and r.rel_err == rel_err:
                    run = r
                    break
            if model == self.na_model:
                axs = na_axs
                ylabel = 'NA inversion'
                [ax.axes.xaxis.set_ticklabels([]) for ax in axs]
            else:
                axs = sp_axs
                ylabel = 'SP inversion'
            for ax in axs:
                ax.invert_yaxis()
                ax.set_ylim(top=0, bottom=530)
                ax.axhline(100, ls=':', c=self.black, zorder=1)
                ax.tick_params(axis='both', which='major', labelsize=12)
            axs[1].set_ylabel(ylabel, fontsize=14, rotation=270,
                              labelpad=20)
            axs[1].yaxis.set_label_position('right')
            axs[0].errorbar(
                run.flux_profiles['sink_S']['est'],
                np.array(model.grid) + 2,
                fmt='o', xerr=run.flux_profiles['sink_S']['err'],
                ecolor=self.blue, c=self.blue, capsize=4,
                label=model.sink_S.label, fillstyle='none',
                elinewidth=1.5, capthick=1.5)

            axs[0].errorbar(
                run.flux_profiles['sink_L']['est'],
                np.array(model.grid) - 2,
                fmt='o', xerr=run.flux_profiles['sink_L']['err'],
                ecolor=self.orange, c=self.orange, capsize=4,
                label=model.sink_L.label, fillstyle='none',
                elinewidth=1.5, capthick=1.5)

            axs[1].tick_params(labelleft=False)
            axs[1].errorbar(
                run.flux_profiles['sink_T']['est'], model.grid, fmt='o',
                xerr=run.flux_profiles['sink_T']['err'],
                ecolor=self.vermillion, c=self.vermillion, capsize=4, zorder=3,
                label=model.sink_T.label, elinewidth=1.5, capthick=1.5,
                fillstyle='none')
            axs[1].errorbar(
                th_flux, th_depths + 4, fmt='^', xerr=th_flux_u,
                ecolor=self.green, c=self.green, capsize=4,
                label='$^{234}$Th-based', elinewidth=1.5, capthick=1.5)
            axs[1].errorbar(
                st_flux, st_depths - 4, fmt='d', xerr=st_flux_u, c=self.black,
                ecolor=self.black, capsize=4, label='Sed. Traps',
                elinewidth=1.5, capthick=1.5)

            axs[0].set_xlim([0, 6])
            axs[1].set_xlim([0, 10])

            if ylabel == 'SP inversion':
                axs[0].legend(loc='lower right', fontsize=12,
                              handletextpad=0.01)
                axs[1].legend(loc='lower right', fontsize=12,
                              handletextpad=0.01)

        fig.savefig('out/sinkfluxes_2model.pdf')
        plt.close()

    def volumetric_fluxes_2model(self, gamma, rel_err):

        fig, (na_axs, sp_axs) = plt.subplots(2, 4, figsize=(7, 6))
        fig.subplots_adjust(left=0.14, right=0.95, top=0.85, bottom=0.17,
                            wspace=0.1)
        fig.text(0.05, 0.5, 'Depth (m)', fontsize=14, ha='center',
                 va='center', rotation='vertical')
        fig.text(0.55, 0.05, 'POC flux (mmol m$^{-3}$ d$^{-1}$)',
                 fontsize=14, ha='center', va='center')

        pairs = (('sinkdiv_S', 'sinkdiv_L'), ('remin_S', 'aggregation'),
                 ('remin_L', 'disaggregation'), ('production',))

        xlims = {'sinkdiv_S': (-0.2, 0.2), 'remin_S': (-0.05, 0.3),
                  'production':(-0.01, 0.25)}


        for model in (self.na_model, self.sp_model):
            for r in model.model_runs:
                if r.gamma == gamma and r.rel_err == rel_err:
                    run = r
                    break
            if model == self.na_model:
                axs = na_axs
                ylabel = 'NA inversion'
            else:
                axs = sp_axs
                ylabel = 'SP inversion'

            for i, pr in enumerate(pairs):
                ax = axs[i]
                if i:
                    ax.tick_params(labelleft=False)
                if i == 3:
                    ax.set_ylabel(ylabel, fontsize=14, rotation=270,
                                  labelpad=20)
                    ax.yaxis.set_label_position('right')
                ax.invert_yaxis()
                ax.tick_params(axis='both', which='major', labelsize=12)
                ax.set_yticks([0, 100, 200, 300, 400, 500])
                if pr[0] != 'remin_L':
                    ax.set_xlim(xlims[pr[0]])
                if pr[0] != 'production':
                    for j, z in enumerate(model.zones):
                        depths = z.depths
                        ax.scatter(
                            run.flux_profiles[pr[0]]['est'][j], np.mean(depths),
                            marker='o', c=self.blue, s=14, zorder=3, lw=0.7,
                            label=eval(f'model.{pr[0]}.label'))
                        ax.fill_betweenx(
                            depths,
                            (run.flux_profiles[pr[0]]['est'][j]
                             - run.flux_profiles[pr[0]]['err'][j]),
                            (run.flux_profiles[pr[0]]['est'][j]
                             + run.flux_profiles[pr[0]]['err'][j]),
                            color=self.blue, alpha=0.25)
                        ax.scatter(
                            run.flux_profiles[pr[1]]['est'][j], np.mean(depths),
                            marker='o', c=self.orange, s=14, zorder=3, lw=0.7,
                            label=eval(f'model.{pr[1]}.label'))
                        ax.fill_betweenx(
                            depths,
                            (run.flux_profiles[pr[1]]['est'][j]
                             - run.flux_profiles[pr[1]]['err'][j]),
                            (run.flux_profiles[pr[1]]['est'][j]
                             + run.flux_profiles[pr[1]]['err'][j]),
                            color=self.orange, alpha=0.25)
                    if i == 0:
                        ax.axvline(0, ls=':', c=self.black, zorder=1)

                else:
                    depths = model.grid
                    df = model.data['NPP']
                    H = model.mld
                    npp = df.loc[df['target_depth'] >= H]['NPP']
                    depth = df.loc[df['target_depth'] >= H]['target_depth']
                    ax.scatter(npp/model.MOLAR_MASS_C, depth, c=self.orange,
                               alpha=0.5, label='NPP', s=10)
                    ax.scatter(
                        run.flux_profiles[pr[0]]['est'], depths, marker='o',
                        c=self.blue, s=14, label=eval(f'model.{pr[0]}.label'),
                        zorder=3, lw=0.7)
                    ax.errorbar(
                        run.flux_profiles[pr[0]]['est'], depths, fmt='o',
                        xerr=run.flux_profiles[pr[0]]['err'], ecolor=self.blue,
                        elinewidth=0.5, c=self.blue, ms=1.5, capsize=2,
                        label=eval(f'model.{pr[0]}.label'), fillstyle='none',
                        markeredgewidth=0.5)
                if ylabel == 'NA inversion':
                    handles, labels = ax.get_legend_handles_labels()
                    unique = [
                        (h, l) for i, (h, l) in enumerate(
                            zip(handles, labels)) if l not in labels[:i]]
                    ax.legend(*zip(*unique), loc='center', fontsize=12,
                              handletextpad=0.01, bbox_to_anchor=(0.45, 1.2),
                              frameon=False)
                else:
                    ax.set_xlabel(('A', 'B', 'C', 'D')[i], fontsize=14)

        fig.savefig('out/fluxes_volumetric_2model.pdf')
        plt.close()

    def dvm_fluxes_2model(self, gamma, rel_err):

        fig = plt.figure()
        fig.text(0.025, 0.5, 'Depth (m)', fontsize=14, ha='center',
                 va='center', rotation='vertical')
        fig.subplots_adjust(wspace=0.3, hspace=0.1)

        for model in (self.na_model, self.sp_model):
            for r in model.model_runs:
                if r.gamma == gamma and r.rel_err == rel_err:
                    run = r
                    break
            if model == self.na_model:
                i = 0
            else:
                i = 1

            hostL = host_subplot(2, 2, 1+2*i, axes_class=AA.Axes, figure=fig)
            parL = hostL.twiny()
            parL.axis['top'].toggle(all=True)
            hostL.set_xlim(0, 0.3)
            parL.set_xlim(0, 0.3)

            hostR = host_subplot(2, 2, 2*(1+i), axes_class=AA.Axes, figure=fig)
            hostR.yaxis.set_ticklabels([])
            parR = hostR.twiny()
            parR.axis['top'].toggle(all=True)
            hostR.set_xlim(-0.02, 0.02)
            parR.set_xlim(0.02, -0.02)
            hostR.axvline(c=self.black, alpha=0.3)

            if model == self.sp_model:
                hostL.set_xlabel('Ingestion flux (mmol m$^{-3}$ d$^{-1}$)')
                hostR.set_xlabel('Excretion flux (mmol m$^{-3}$ d$^{-1}$)')
                hostR.text(1.05, 0.2, 'SP inversion' , fontsize=14,
                          rotation=270, transform=hostR.transAxes)
                parR.xaxis.set_ticklabels([])
                parL.xaxis.set_ticklabels([])
            else:
                parL.set_xlabel('$P_S$ remin. flux (mmol m$^{-3}$ d$^{-1}$)')
                parR.set_xlabel('$P_L$ SFD (mmol m$^{-3}$ d$^{-1}$)')
                hostR.xaxis.set_ticklabels([])
                hostL.xaxis.set_ticklabels([])
                hostR.text(1.05, 0.2, 'NA inversion' , fontsize=14,
                          rotation=270, transform=hostR.transAxes)

            for host, par in ((hostL, parL), (hostR, parR)):
                host.axis['right'].toggle(all=False)
                host.axis['left', 'top', 'bottom'].major_ticks.set_tick_out(
                    'out')
                par.axis['left', 'top', 'bottom'].major_ticks.set_tick_out(
                    'out')
                host.axis['bottom'].label.set_color(self.blue)
                par.axis['top'].label.set_color(self.orange)
                host.axis['left'].label.set_fontsize(14)
                host.axis['bottom'].label.set_fontsize(12)
                host.axis['bottom', 'left'].major_ticklabels.set_size(12)
                par.axis['top'].label.set_fontsize(12)
                par.axis['top'].major_ticklabels.set_size(12)

            for j, z in enumerate(model.zones):
                if j < 3:
                    host = hostL
                    par = parL
                    par_flux = 'remin_S'
                else:
                    host = hostR
                    par = parR
                    par_flux = 'sinkdiv_L'
                depths = z.depths
                host.scatter(
                    run.flux_profiles['dvm']['est'][j], np.mean(depths),
                    marker='o', c=self.blue, s=14, zorder=3, lw=0.7)
                host.fill_betweenx(
                    depths,
                    (run.flux_profiles['dvm']['est'][j]
                     - run.flux_profiles['dvm']['err'][j]),
                    (run.flux_profiles['dvm']['est'][j]
                     + run.flux_profiles['dvm']['err'][j]),
                    color=self.blue, alpha=0.25)
                par.scatter(
                    run.flux_profiles[par_flux]['est'][j], np.mean(depths),
                    marker='o', c=self.orange, s=14, zorder=3, lw=0.7)
                par.fill_betweenx(
                    depths,
                    (run.flux_profiles[par_flux]['est'][j]
                     - run.flux_profiles[par_flux]['err'][j]),
                    (run.flux_profiles[par_flux]['est'][j]
                     + run.flux_profiles[par_flux]['err'][j]),
                    color=self.orange, alpha=0.25)
            for ax in (hostL, hostR):
                ax.set_yticks([0, 100, 200, 300, 400, 500])
                ax.invert_yaxis()
                ax.set_ylim(top=0, bottom=510)
                ax.axhline(100, ls=':', c=self.black)

            fig.savefig('out/dvmflux_2model.pdf')
            plt.close()

if __name__ == '__main__':

    sys.setrecursionlimit(100000)
    start_time = time.time()

    gammas = [0.5, 1, 5, 10]
    rel_errs = [0.1, 0.2, 0.5, 1]

    gam_re = (gammas, rel_errs)

    model_na = PyriteModel(gam_re, priors_from='NA')
    model_sp = PyriteModel(gam_re, priors_from='SP')

    PlotterModelRuns('out/POC_modelruns_dvmTrue_NA.pkl')
    PlotterModelRuns('out/POC_modelruns_dvmTrue_SP.pkl')

    twinX_na = PyriteTwinX(([0.5], [0.5]), 'out/POC_modelruns_dvmTrue_NA.pkl')
    twinX_sp = PyriteTwinX(([0.5], [0.5]), 'out/POC_modelruns_dvmTrue_SP.pkl')

    PlotterTwinX('out/POC_twinX_dvmTrue_NA.pkl')
    PlotterTwinX('out/POC_twinX_dvmTrue_SP.pkl')

    PlotterTwoModel('out/POC_modelruns_dvmTrue_NA.pkl',
                    'out/POC_modelruns_dvmTrue_SP.pkl')

    print(f'--- {(time.time() - start_time)/60} minutes ---')