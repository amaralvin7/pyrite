import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

df = pd.read_csv('../../results/geotraces/mc/table.csv')
good = df.loc[df['success'] == True].copy()
bad = df.loc[df['success'] == False].copy()

# print(len(good)/len(df))  # 0.514
params = ('B2p', 'Bm2', 'Bm1s', 'Bm1l', 'ws', 'wl')

for p in params:
    data = good[p]
    fig, ax = plt.subplots(tight_layout=True)
    ax.hist(data, bins=30)
    fig.savefig(f'../../results/geotraces/mc_0p01/hist_{p}')
    plt.close()

