import os
import pickle
from itertools import product
from time import time
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.colors import Normalize
from matplotlib import cm
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm

import src.geotraces.data as data
from src.colors import *

def load_data(path):
    """Loads data and returns dfs with all sets and successful sets."""
    with open(os.path.join(path, 'table.pkl'), 'rb') as f:
        sets = pickle.load(f)
    good = sets.loc[sets['set_success'] == True].copy()

    return sets, good


def plot_histograms(path, df, params, file_prefix):
    """Plot histogram of values for all parameters in a df."""
    for p in params:
        data = df[p]
        plt.subplots(tight_layout=True)
        plt.hist(data, bins=30)
        plt.savefig(os.path.join(path, f'{file_prefix}_{p}'))
        plt.close()

def stacked_histograms(path, df, params):
    """Stacked histograms for after param sets have been clustered."""
    n_clusters = len(df['label'].unique())
    for p in params:
        df.pivot(columns='label')[p].plot(kind='hist', stacked=True, bins=30)
        plt.savefig(os.path.join(path, f'stackedhist_{n_clusters}_{p}'))
        plt.close()

def elbow_plot(path, df):
    
    df_scaled = StandardScaler().fit_transform(df)
    inertia = []
    
    k_vals = range(1, 21)
    for i in k_vals:
        kmeans = KMeans(n_clusters=i, random_state=0)
        result = kmeans.fit(df_scaled)
        inertia.append(result.inertia_)

    plt.figure()
    plt.plot(k_vals, inertia, marker='o', ls='--')
    plt.xlabel('Number of Clusters')
    plt.xticks(k_vals)
    plt.ylabel('Inertia')
    plt.savefig(os.path.join(path, 'elbowplot'))
    plt.close()

def cluster(df, n_clusters):

    df_scaled = StandardScaler().fit_transform(df)

    kmeans = KMeans(n_clusters=n_clusters, random_state=0)
    result = kmeans.fit(df_scaled)
    df['label'] = result.labels_
    
    unique_labels = df['label'].unique()
    unique_labels.sort()
    for l in unique_labels:
        label_df = df[df['label'] == l]
        print(f'Fraction of successful sets in cluster {l}: {len(label_df)/len(df):.2f}')
    
    return df

def pairplot(path, df):

    df = df[['B2p', 'Bm2', 'Bm1s', 'Bm1l', 'ws', 'wl']]
    sns.pairplot(df)
    plt.savefig(os.path.join(path, 'pairplot'))
    plt.close()

def hist_success(path, filenames):
    """Plot # of succesful inversions for each station."""
    stations = list(data.poc_by_station().keys())
    stations.sort()
    d = {s: len([i for i in filenames if f'stn{s}.pkl' in i]) for s in stations}
    plt.bar(range(len(d)), list(d.values()), align='center')
    plt.xticks(range(len(d)), list(d.keys()))
    plt.savefig(os.path.join(path, 'figs/hist_success'))
    plt.close()

def hist_stats(path, filenames, suffix=''):

    stations = list(data.poc_by_station().keys())
    stations.sort()
    
    dv_params = ('B2p', 'Bm2', 'Bm1s', 'Bm1l', 'ws', 'wl')
    dc_params = ('Po', 'Lp', 'zm', 'a', 'B3')
    
    d = {p: {s: {e: [] for e in ('prior', 'posterior')} for s in stations} for p in dv_params + dc_params}
    
    for f in filenames:
        s = int(f.split('.')[0].split('_')[1][3:])
        with open(os.path.join(path, f), 'rb') as file:
            results = pickle.load(file)
        for p in dv_params:
            d[p][s]['posterior'].extend(results['params'][p]['posterior'])
            d[p][s]['prior'].append(results['params'][p]['prior'])
        for p in dc_params:
            d[p][s]['posterior'].append(results['params'][p]['posterior'])
            d[p][s]['prior'].append(results['params'][p]['prior'])
    
    for p in dc_params + dv_params:
        fig, ax = plt.subplots(tight_layout=True)
        ax.boxplot([d[p][s]['posterior'] for s in stations], positions=range(len(stations)))
        ax.set_xticks(range(len(stations)), d[p].keys())
        if p in dc_params:
            ax.plot(range(len(stations)), [d[p][s]['prior'][0] for s in stations], marker='*', c='b', ls='None')
        else:
            ax.plot(range(len(stations)), [min(d[p][s]['prior']) for s in stations], marker='*', c='b', ls='None')
            ax.plot(range(len(stations)), [max(d[p][s]['prior']) for s in stations], marker='*', c='b', ls='None')
            
        fig.savefig(os.path.join(path, f'figs/hist_{p}{suffix}'))
        plt.close()


def stationparam_hists(path, params, filenames):
    
    dv_params = params
    dc_params = ('Po', 'Lp', 'B3', 'a', 'zm')
    all_params = dv_params + dc_params
    stations = data.poc_by_station().keys()
    data = {s: {p: {'priors': [], 'posteriors': []} for p in all_params} for s in stations}

    for f in filenames:
        with open(os.path.join(path, f), 'rb') as file:
            results = pickle.load(file)
            _, stn = f.split('.')[0].split('_')
            s = int(stn[3:])
            for p in all_params:
                data[s][p]['priors'].append(results['params'][p]['prior'])
                if p in dv_params:
                    data[s][p]['posteriors'].extend(results['params'][p]['posterior'])
                else:
                    data[s][p]['posteriors'].append(results['params'][p]['posterior'])
    
    for (s, p) in product(stations, dv_params):
        _, axs = plt.subplots(1, 2, tight_layout=True)
        axs[0].hist(data[s][p]['priors'], bins=30)
        axs[1].hist(data[s][p]['posteriors'], bins=30)
        x_lo = min([a.get_xlim()[0] for a in axs])
        x_hi = max([a.get_xlim()[1] for a in axs])
        for a in axs:
            a.set_xlim(x_lo, x_hi)
        plt.savefig(os.path.join(path, f'figs/sp_hist_{s}_{p}'))
        plt.close()

    for (s, p) in product(stations, dc_params):
        _, ax = plt.subplots(tight_layout=True)
        ax.axvline(data[s][p]['priors'][0], c='k')
        ax.hist(data[s][p]['posteriors'], bins=30)
        plt.savefig(os.path.join(path, f'figs/sp_hist_{s}_{p}'))
        plt.close()

def get_filenames(path, successful_sets=False):

    pickled_files = [f for f in os.listdir(path) if 'stn' in f]
    pickled_files.sort(key = lambda x: int(x.split('_')[0][2:]))

    if successful_sets == True:
        stations = data.poc_by_station().keys()
        filenames = []
        set_number = 0
        set_counter = 0
        for i, f in enumerate(pickled_files):
            f_set = int(f.split('_')[0][2:])
            if f_set == set_number:
                set_counter += 1
                if set_counter == len(stations):
                    filenames.extend(pickled_files[i+1-len(stations):i+1])
            else:
                set_number += 1
                set_counter = 1
        print(f'N successful sets: {len(filenames) / len(stations)}')
        print(f'N successful inversions: {len(pickled_files)}')
    else:
        filenames = pickled_files

    return filenames

def xresids(path, station_data):
    
    df = pd.DataFrame(columns=['resid', 'element'])

    for stn in poc_data.keys():
        resids = []
        elements = []
        pickled_files = [f for f in os.listdir(path) if f'stn{stn}.pkl' in f]
        for f in pickled_files:
            with open(os.path.join(path, f), 'rb') as file:
                results = pickle.load(file)
                _, stn = f.split('.')[0].split('_')
                s = int(stn[3:])
                resids.extend(results['x_resids'])
                elements.extend([s.split('_')[0] for s in station_data[s]['s_elements']])

        _, axs = plt.subplots(1, 2, tight_layout=True)
        
        df = pd.DataFrame({'resid': resids, 'element': elements})
        dv_elements = ('B2p', 'Bm2', 'Bm1s', 'Bm1l', 'ws', 'wl', 'POCS', 'POCL')
        df1 = df[df['element'].isin(dv_elements)]
        df1_piv = df1.pivot(columns='element')['resid'].astype(float)
        df2 = df[~df['element'].isin(dv_elements)]
        df2_piv = df2.pivot(columns='element')['resid'].astype(float)
        df1_piv.plot(kind='hist', stacked=True, bins=30, ax=axs[0])
        df2_piv.plot(kind='hist', stacked=True, bins=30, ax=ax)
        plt.savefig(os.path.join(path, f'figs/xresids_{stn}'))
        plt.close()

def compile_param_estimates(params, filenames):

    df_rows = []

    for f in tqdm(filenames):
        with open(os.path.join(path, f), 'rb') as file:
            results = pickle.load(file)['params']
            stn = int(f.split('.')[0].split('_')[1][3:])
            file_dict = {p: results[p]['posterior'] for p in params}
            file_dict['depth'] = station_data[stn]['grid']
            file_dict['latitude'] = station_data[stn]['latitude'] * np.ones(len(station_data[stn]['grid']))
            file_dict['station'] = stn * np.ones(len(station_data[stn]['grid']))
            df_rows.append(pd.DataFrame(file_dict))
    df = pd.concat(df_rows, ignore_index=True)
    
    with open(os.path.join(path, 'saved_params.pkl'), 'wb') as f:
        pickle.dump(df, f)
            
def param_sections(path, station_data, suffix=''):

    with open(os.path.join(path, 'saved_params.pkl'), 'rb') as f:
        df = pickle.load(f)    

    params = ('B2p', 'Bm2', 'Bm1s', 'Bm1l', 'ws', 'wl')
    scheme = plt.cm.viridis
    lats = [station_data[s]['latitude'] for s in station_data]
    mlds_unsorted = [station_data[s]['mld'] for s in station_data]
    zgs_unsorted = [station_data[s]['zg'] for s in station_data]
    mlds = [mld for _, mld in sorted(zip(lats, mlds_unsorted))]
    zgs = [zg for _, zg in sorted(zip(lats, zgs_unsorted))]
    lats.sort()
    
    for p in params:
        p_df = df[['depth', 'station', p]]
        mean = p_df.groupby(['depth', 'station']).mean().reset_index()
        sd = p_df.groupby(['depth', 'station']).std().reset_index()
        merged = mean.merge(sd, suffixes=(None, '_sd'), on=['depth', 'station'])
        merged[f'{p}_cv'] = merged[f'{p}_sd'] / merged[p]
        
        fig, axs = plt.subplots(2, 1, figsize=(10, 5), tight_layout=True)
        for i, ax in enumerate(axs):
            ax.invert_xaxis()
            ax.invert_yaxis()
            ax.set_ylabel('Depth (m)', fontsize=14)
            ax.scatter(merged['latitude'], merged['depth'], c='k', zorder=1, s=1)
            ax.plot(lats, mlds, c='k', zorder=1, ls='--')
            ax.plot(lats, zgs, c='k', zorder=1)
            if i == 0:
                cbar_label = 'Mean'
                to_plot = merged[p]
                for s, d in station_data.items():
                    ax.text(d['latitude'], -30, s, ha='center')
            else:
                cbar_label = 'CoV'
                to_plot = merged[f'{p}_cv']
                ax.set_xlabel('Latitude (°N)', fontsize=14)
            norm = Normalize(to_plot.min(), to_plot.max())
            cbar = fig.colorbar(cm.ScalarMappable(norm=norm, cmap=scheme), ax=ax, pad=0.01)
            cbar.set_label(cbar_label, rotation=270, labelpad=20, fontsize=14)
            ax.scatter(merged['latitude'], merged['depth'], c=to_plot, norm=norm, cmap=scheme, zorder=10)
        fig.savefig(os.path.join(path, f'figs/section_{p}{suffix}.pdf'))
        plt.close()

def flux_profiles(path, filenames, station_data):

    df_rows = []

    for f in tqdm(filenames):
        with open(os.path.join(path, f), 'rb') as file:
            results = pickle.load(file)
            stn = int(f.split('.')[0].split('_')[1][3:])
            file_dict = {'depth': station_data[stn]['grid'],
                         'station': stn * np.ones(len(station_data[stn]['grid'])),
                         'ws': np.array(results['params']['ws']['posterior']),
                         'wl': np.array(results['params']['wl']['posterior']),
                         'POCS': np.array(results['tracers']['POCS']['posterior']),
                         'POCL': np.array(results['tracers']['POCL']['posterior'])}
            df_rows.append(pd.DataFrame(file_dict))
    df = pd.concat(df_rows, ignore_index=True)

    df['sflux'] = df['ws'] * df['POCS']
    df['lflux'] = df['wl'] * df['POCL']
    df['tflux'] = df['sflux'] + df['lflux']
    df.drop(['ws', 'wl', 'POCS', 'POCL'], axis=1, inplace=True)
    mean = df.groupby(['depth', 'station']).mean().reset_index()
    sd = df.groupby(['depth', 'station']).std().reset_index()
    pump_fluxes = mean.merge(sd, suffixes=(None, '_sd'), on=['depth', 'station'])
    th234_fluxes = pd.read_csv('../../../geotraces/pocfluxes_from_th234.csv')
    
    for s in pump_fluxes['station'].unique():
        pf_s = pump_fluxes[pump_fluxes['station'] == s]
        tf_s = th234_fluxes[th234_fluxes['station'] == s].iloc[0]
        zg = station_data[s]['zg']
        mld = station_data[s]['mld']
        _, ax = plt.subplots(tight_layout=True)

        ax.errorbar(pf_s['tflux'], pf_s['depth'], fmt='o', xerr=pf_s['tflux_sd'],
            ecolor=vermillion, c=vermillion, capsize=4, zorder=3,
            label='$w_TP_T$', elinewidth=1.5, capthick=1.5,
            fillstyle='none')
        ax.errorbar(pf_s['sflux'], pf_s['depth'] + 5, fmt='o', xerr=pf_s['sflux_sd'],
            ecolor=blue, c=blue, capsize=4, zorder=3,
            label='$w_SP_S$', elinewidth=1.5, capthick=1.5,
            fillstyle='none')
        ax.errorbar(pf_s['lflux'], pf_s['depth'] - 5, fmt='o', xerr=pf_s['lflux_sd'],
            ecolor=orange, c=orange, capsize=4, zorder=3,
            label='$w_LP_L$', elinewidth=1.5, capthick=1.5,
            fillstyle='none')
        ax.errorbar([tf_s['ppz'], tf_s['100m']], [tf_s['ppzd'], 100], fmt='o',
                    xerr=[tf_s['ppz_e'], tf_s['100m_e']], ecolor=green,
                    c=green, capsize=4, zorder=3,
                    label='$^{234}$Th-based', elinewidth=1.5, capthick=1.5,
                    fillstyle='none')   

        ax.set_ylabel('Depth (m)', fontsize=14)
        ax.set_xlabel('Flux (mmol m$^{-2}$ d$^{-1}$)', fontsize=14)
        ax.invert_yaxis()
        ax.set_ylim(top=0, bottom=610)
        ax.tick_params(axis='both', which='major', labelsize=12)
        ax.axhline(zg, c='k', ls=':')
        ax.axhline(mld, c='k', ls='--')
        ax.legend(loc='lower right', fontsize=12, handletextpad=0.01)
        
        plt.savefig(os.path.join(path, f'figs/sinkfluxes_stn{int(s)}'))
        plt.close()


def regress(path, params):

    with open(os.path.join(path, 'saved_params.pkl'), 'rb') as f:
        df = pickle.load(f)

    odf_data = pd.read_csv('../../../geotraces/ODFpump.csv',
                           usecols=['Station', 'CorrectedMeanDepthm', 'CTDTMP_T_VALUE_SENSORdegC', 'CTDOXY_D_CONC_SENSORumolkg'])
    odf_data = odf_data.rename({'Station': 'station',
                                'CorrectedMeanDepthm': 'depth',
                                'CTDTMP_T_VALUE_SENSORdegC': 'T',
                                'CTDOXY_D_CONC_SENSORumolkg': 'O2'}, axis='columns')

    param_means = df.groupby(['depth', 'station']).mean().reset_index()
    merged = param_means.merge(odf_data)

    for p in params:
        sns.scatterplot(x=p, y='T', data=merged, hue='depth')
        plt.savefig(os.path.join(path, f'figs/scatter_{p}_T'))
        plt.close()
        sns.scatterplot(x=p, y='O2', data=merged, hue='depth')
        plt.savefig(os.path.join(path, f'figs/scatter_{p}_O2'))
        plt.close()

def param_profile_distribution(path, param):

    with open(os.path.join(path, 'saved_params.pkl'), 'rb') as f:
        df = pickle.load(f)
    
    for s in df['station'].unique():
        sdf = df[df['station'] == s]
        depths = sdf['depth'].unique()
        _, axs = plt.subplots(len(depths), 1, tight_layout=True, figsize=(5,10))
        for i, d in enumerate(depths):
            ddf = sdf[sdf['depth'] == d]
            axs[i].hist(ddf[param])
            axs[i].set_ylabel(f'{d:.0f} m')
            axs[i].axvline(ddf[param].mean(), c=black, ls='--')
            axs[i].axvline(ddf[param].median(), c=black, ls=':')
        plt.savefig(os.path.join(path, f'figs/ppd_{param}_stn{int(s)}'))
        plt.close()

if __name__ == '__main__':
    
    start_time = time()

    poc_data = data.poc_by_station()
    param_uniformity = data.define_param_uniformity()
    Lp_priors = data.get_Lp_priors(poc_data)
    ez_depths = data.get_ez_depths(Lp_priors)
    station_data = data.get_station_data(poc_data, param_uniformity, ez_depths)
    
    n_sets = 125000
    path = f'../../results/geotraces/mc_{n_sets}'
    params = ('B2p', 'Bm2', 'Bm1s', 'Bm1l', 'ws', 'wl')
    all_files = get_filenames(path)
    flux_profiles(path, all_files, station_data)
            
    print(f'--- {(time() - start_time)/60} minutes ---')

    