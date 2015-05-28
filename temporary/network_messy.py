import numpy as np
import pandas as pd
import networkx as nx
import geopandas as gpd
import shapely

# class network_graph():
#     def __init__(self, lines, subs, loads, transfers='infer'):
#         t = gpd.read_file(lines)
#         s = gpd.read_file(subs)
#         L = gpd.read_file(loads)


#### SPECIFY SHAPEFILES

translines = '/home/akagi/Desktop/electricity_data/Transmission_Lines.shp'
t = gpd.read_file(translines)

substations = '/home/akagi/Desktop/electricity_data/Substations.shp'
s = gpd.read_file(substations)

loads_shp = '/home/akagi/voronoi_stats.shp'
L = gpd.read_file(loads_shp)

g = '/home/akagi/Desktop/electricity_data/Generation.shp'
g = gpd.read_file(g)

#### LINE LENGTHS

linelength = t.set_index('UNIQUE_ID').to_crs(epsg=2762).length
linelength.name = 'length_m'

#### EDGES

edges = pd.read_csv('edges.csv', index_col=0)
# This can probably be done when edges.csv is generated VVV
edges = pd.concat([edges.set_index('TRANS_ID'), linelength], axis=1).reset_index()

edges['TOT_CAP_KV'][edges['TOT_CAP_KV'] <= 0] = 69
#### GENERATION

gen = pd.read_csv('gen_to_sub_static.csv', index_col=0) 

#### NET DEMAND

#net = pd.concat([L.groupby('SUB_ID').sum()['summer_loa'], gen.groupby('SUB_ID').sum()['S_CAP_MW'].fillna(0)], axis=1, join='outer')[['summer_loa', 'S_CAP_MW']].fillna(0)

#net = net['S_CAP_MW'] - net['summer_loa']

#### SEPARATE SUB AND GEN; NEED FOR CURRENT

subloads = L.groupby('SUB_ID').sum()['summer_loa'].reindex(s['UNIQUE_ID'].values).fillna(0)
subgen = gen.groupby('SUB_ID').sum()['S_CAP_MW'].reindex(s['UNIQUE_ID'].values).fillna(0)
net = subgen - subloads

#### PHOENIX

phx_bbox = np.array([-112.690, 32.670, -111.192, 34.138]).reshape(2,2)
phx_poly = shapely.geometry.MultiPoint(np.vstack(np.dstack(np.meshgrid(*np.hsplit(phx_bbox, 2)))).tolist()).convex_hull

phx_lines = t[t.intersects(phx_poly)]['UNIQUE_ID'].astype(int).values

phx_edges = edges[edges['TRANS_ID'].isin(phx_lines)]

phx_edges = phx_edges[phx_edges['SUB_1'] != phx_edges['SUB_2']]

phx_nodes = np.unique(phx_edges[['SUB_1', 'SUB_2']].values.astype(int).ravel())

# OUTER LINES

edgesubs = pd.merge(t[t.intersects(phx_poly.boundary)], edges, left_on='UNIQUE_ID', right_on='TRANS_ID')[['SUB_1_y', 'SUB_2_y']].values.ravel().astype(int)

# NODES JUST OUTSIDE OF BBOX (ENTERING)
outer_nodes = np.unique(edgesubs[~np.in1d(edgesubs, s[s.within(phx_poly)]['UNIQUE_ID'].values.astype(int))])

weights = s.loc[s['UNIQUE_ID'].astype(int).isin(edgesubs[~np.in1d(edgesubs, s[s.within(phx_poly)]['UNIQUE_ID'].values.astype(int))])].set_index('UNIQUE_ID')['MAX_VOLT'].sort_index()

transfers = net[phx_nodes].sum()*(weights/weights.sum()).reindex(s['UNIQUE_ID'].values).fillna(0)

phx_loads = net[phx_nodes] + transfers.reindex(phx_nodes).fillna(0)

####

G = nx.Graph()

for i in phx_nodes:
    G.add_node(i,
    load = subloads[i],
    gen = subgen[i],
    trans = transfers[i],
    MW_net = subgen[i] - subloads[i] - transfers[i],
    I_load = 1000*subloads[i]/69.0,
    I_gen = 1000*subgen[i]/69.0,
    I_trans = 1000*transfers[i]/69.0,
    I_net = 1000*subgen[i]/69.0 - 1000*subloads[i]/69.0 - 1000*transfers[i]/69.0
)

for i in phx_edges.index:
    row = phx_edges.loc[i]
    G.add_edge(*tuple(row[['SUB_1', 'SUB_2']].astype(int).values),
		tot_kv=row['TOT_CAP_KV'],
		num_lines=int(row['NUM_LINES']),
		length=row['length_m'])

import cable

cable_classes = {
    525 : {0 : ['Chukar', 'acsr'],
           1 : ['Bluebird', 'acsr']},
    345 : {0 : ['Tern', 'acsr']},
    230 : {0 : ['Bittern', 'acsr'],
           1 : ['Bluebird', 'acss'],
           2 : ['Tern', 'acsr']},
    115 : {0 : ['Bittern', 'acsr'],
           1 : ['Bluebird', 'acss'],
           2 : ['Tern', 'acsr']},
    69  : {0 : ['Tern', 'acss'],
           1 : ['Arbutus', 'aac'],
           2 : ['Linnet', 'acsr']}
    }

instance_cables = {
    525 : cable.cable(*cable_classes[525][0]),
    345 : cable.cable(*cable_classes[345][0]),
    230 : cable.cable(*cable_classes[230][0]),
    115 : cable.cable(*cable_classes[115][0]),
    69 :  cable.cable(*cable_classes[69][0])}

v_list = np.array([69, 115, 230, 345, 525])

for i in G.edges():
    props = G[i[0]][i[1]]
    kv = props['tot_kv']/float(props['num_lines'])
    v_class = v_list[np.argmin(abs(v_list - 500.0))]
    cname = cable_classes[v_class][0][0]
    model = cable_classes[v_class][0][1]
    R = instance_cables[v_class].R.mean()
    G.edge[i[0]][i[1]]['R_unit'] = R
    G.edge[i[0]][i[1]]['R'] = R*props['length']
    G.edge[i[0]][i[1]]['amp_75'] = instance_cables[v_class].models[model][cname]['amp_75']
    G.edge[i[0]][i[1]]['tot_amp_75'] = G[i[0]][i[1]]['amp_75']*int(G[i[0]][i[1]]['num_lines'])
    G.edge[i[0]][i[1]]['tot_MW_cap'] = (G[i[0]][i[1]]['tot_amp_75']/1000.0)*G[i[0]][i[1]]['tot_kv']


#A = np.array([G.node[i]['I_net'] if 'I_net' in G.node[i].keys() else 0 for i in G.nodes()])

#### LINALG SOLVER

cycles = nx.cycle_basis(G)
cycles = np.array([[tuple([cycles[j][i], cycles[j][i+1]]) if (i < len(cycles[j])-1) else tuple([cycles[j][i], cycles[j][0]]) for i in range(len(cycles[j]))] for j in range(len(cycles))])

#L = [G.node[i]['demand'] for i in G.node.keys()]
### NEED A NEW NAME FOR edges
edges = np.array(G.edges())
# Problem because nodes are no longer integer indexed VVV
### QUICKFIX
node_idx = pd.Series(range(len(G.nodes())), index=G.nodes())
# edge_idx = np.full((len(G), len(G)), 9999, dtype=int)
edge_idx[node_idx[edges[:,0]].values, node_idx[edges[:,1]].values] = np.arange(len(G.edges()))
edge_idx[node_idx[edges[:,1]].values, node_idx[edges[:,0]].values] = np.arange(len(G.edges()))

edge_dir = np.zeros((len(G), len(G)), dtype=int)
edge_dir[node_idx[edges[:,0]].values, node_idx[edges[:,1]].values] = 1
edge_dir[node_idx[edges[:,1]].values, node_idx[edges[:,0]].values] = -1

X = nx.incidence_matrix(G, oriented=True).toarray()
S = np.array([G.node[i]['I_net'] if 'I_net' in G.node[i].keys() else 0 for i in G.nodes()])

for u in cycles:

    R = np.array([G[j[0]][j[1]]['R'] for j in u])
#    V = np.array([G[j[0]][j[1]]['tot_kv'] for j in u])
    u = np.vstack(u)
    u = np.column_stack([node_idx[u[:,0]], node_idx[u[:,1]]])
    D = np.array(edge_dir[u[:,0], u[:,1]])
    z = np.zeros(len(edges))
    z[edge_idx[u[:,0], u[:,1]]] = R*D
    X = np.vstack([X, z])
    S = np.append(S, 0)

sol = scipy.linalg.lstsq(X, S)

#### NETWORK SIMPLEX SOLVER
CG = G.subgraph(list(nx.connected_components(G))[0])

C_gen = subgen.loc[CG.nodes()].round().astype(int)
C_loads = subloads.loc[CG.nodes()].round().astype(int)

weights = s.loc[s['UNIQUE_ID'].astype(int).isin(edgesubs[~np.in1d(edgesubs, s[s.within(phx_poly)]['UNIQUE_ID'].values.astype(int))])].set_index('UNIQUE_ID').loc[CG.nodes()]['MAX_VOLT'].dropna().sort_index()

C_transfers = ((C_gen.sum() - C_loads.sum())*(weights/weights.sum()).loc[CG.nodes()].fillna(0)).astype(int)

if C_transfers.sum() != (C_gen.sum() - C_loads.sum()):
    dif = (C_transfers.sum() - (C_gen.sum() - C_loads.sum()))
    direct = 1 if dif > 0 else -1
    nz_idx = pd.Series(np.nonzero(C_transfers.values)[0])
    for i in range(abs(dif)):
        C_transfers.iloc[nz_idx[i]] -= direct 

for i in CG.nodes():
    CG.node[i]['load'] = C_loads[i]
    CG.node[i]['gen'] = C_gen[i]
    CG.node[i]['trans'] = C_transfers[i]

for i in CG.nodes():
    CG.node[i]['MW_net'] = CG.node[i]['gen'] - CG.node[i]['load'] - CG.node[i]['trans']

DG = CG.to_directed()


NS = nx.network_simplex(DG, demand='MW_net', weight='R', capacity='tot_MW_cap')
#### EVERYTHING BELOW DOESN'T WORK!!!

for i in G.nodes():
    kv_list = [G.adj[i][j]['tot_kv'] for j in G.adj[i].keys() if isinstance(j, int)]
    kv_max, kv_min = max(kv_list), min(kv_list)
    G[i]['max_volt'] = kv_max
    G[i]['min_volt'] = kv_min

#### GET GRID VOLTAGES FROM EIA FORM DATA

mwkv = pd.DataFrame(np.zeros(len(G.nodes())), index=G.nodes())

for x in ['load', 'gen', 'trans', 'min_volt', 'max_volt']:
    mwkv_col = pd.DataFrame(np.vstack([tuple([i, G[i][x]]) for i in G.nodes() if x in G[i].keys()])).rename(columns={1 : x}).set_index(0)
    mwkv = pd.concat([mwkv, mwkv_col], axis=1)

mwkv.replace(-99, 69, inplace=True)
mwkv['max_volt'][mwkv['max_volt'] < 69] = 69
mwkv['min_volt'][mwkv['min_volt'] < 69] = 69
mwkv[['min_volt', 'max_volt']].fillna(69, inplace=True)
mwkv['I_load'] = (1000*mwkv['load']/mwkv['min_volt'])
mwkv['I_gen'] = (1000*mwkv['gen']/mwkv['max_volt'])
mwkv['I_trans'] = (1000*mwkv['trans']/mwkv['max_volt'])

# plant_860 = pd.read_excel('/home/akagi/Documents/EIA_form_data/eia8602012/PlantY2012.xlsx', header=1)
# gen_860 = pd.read_excel('/home/akagi/Documents/EIA_form_data/eia8602012/GeneratorY2012.xlsx', sheetname='Operable', header=1)

# plant_cap = pd.merge(plant_860, gen_860, on='Plant Code').groupby('Plant Code').sum()[['Summer Capacity (MW)', 'Winter Capacity (MW)', 'Nameplate Capacity (MW)']]
# plant_chars = plant_860.set_index('Plant Code')[['Plant Name', 'Utility ID', 'NERC Region', 'Grid Voltage (kV)', 'Latitude', 'Longitude']]
# g_dyn = pd.concat([plant_cap, plant_chars], axis=1).dropna(subset=['Longitude', 'Latitude', 'Grid Voltage (kV)'])

# tree = spatial.cKDTree(np.vstack(g.geometry.apply(lambda x: np.concatenate(x.xy)).values))
# tree_query = tree.query(g_dyn[['Longitude', 'Latitude']].values)

# gridvolts = g.iloc[tree_query[1]]['UNIQUE_ID']
# gridvolts = pd.DataFrame(np.column_stack([gridvolts.reset_index().values, g_dyn['Grid Voltage (kV)'].values.astype(float)])).drop_duplicates(1).set_index(1)[2]
# gridvolts.index = gridvolts.index.values.astype(int)

# gen = pd.concat([gridvolts, gen.set_index('GEN_ID')], axis=1).reset_index().rename(columns={2:'Grid_KV', 'index':'GEN_ID'})

#### 
#s.loc[s['UNIQUE_ID'].astype(int).isin(outer_nodes)].plot()
plot(phx_poly.exterior.xy[0], phx_poly.exterior.xy[1])
for i in nx.connected_components(G):
    geom = s.set_index('UNIQUE_ID').loc[i].dropna()
    geom['geometry'].plot()

#### DEBUGGING CONNECTED COMPONENTS

cc = list(nx.connected_components(G))
ss = edges[['SUB_1', 'SUB_2']].astype(int)

# Each disconnected component in cc has one or more subs with a missing point of entry into system
# This is caused by orphaned subgraphs being created when the bbox is drawn.
# Probably can't be fixed -- will simply have to remove disconnected components


#### LINALG SOLVER


cycles = nx.cycle_basis(G)
cycles = np.array([[tuple([cycles[j][i], cycles[j][i+1]]) if (i < len(cycles[j])-1) else tuple([cycles[j][i], cycles[j][0]]) for i in range(len(cycles[j]))] for j in range(len(cycles))])

L = [G.node[i]['demand'] for i in G.node.keys()]
edges = np.array(G.edges())
edge_idx = np.full((len(G), len(G)), 9999, dtype=int)
edge_idx[edges[:,0], edges[:,1]] = np.arange(len(G.edges()))
edge_idx[edges[:,1], edges[:,0]] = np.arange(len(G.edges()))

edge_dir = np.zeros((len(G), len(G)), dtype=int)
edge_dir[edges[:,0], edges[:,1]] = 1
edge_dir[edges[:,1], edges[:,0]] = -1

X = nx.incidence_matrix(G, oriented=True).toarray()
S = np.array(loads)

for u in cycles:

#    R = np.array([G[j[0]][j[1]]['resistance'] for j in u])
    V = np.array([G[j[0]][j[1]]['tot_kv'] for j in u])
    D = np.array(edge_dir[u[:,0], u[:,1]])
    z = np.zeros(len(edges))
    z[edge_idx[u[:,0], u[:,1]]] = R*D
    X = np.vstack([X, z])
    S = np.append(S, 0)

scipy.linalg.lstsq(X, S)
