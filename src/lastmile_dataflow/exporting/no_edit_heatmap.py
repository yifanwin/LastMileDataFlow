"""Actual measured rates → ground-plane geodesic Gaussian heatmaps, PNG/SVG/NPZ."""
from pathlib import Path
import numpy as np

from ..io import write_json
from ..navigation.astar import distance_field


def gaussian_map(grid, rows, sigma, *, max_support_distance_m=.35):
    if not np.isfinite(sigma) or sigma <= 0:
        raise ValueError('invalid Gaussian bandwidth')
    if not np.isfinite(max_support_distance_m) or max_support_distance_m <= 0:
        raise ValueError('invalid Gaussian support distance')
    numerator = np.zeros(grid.free.shape); denominator = np.zeros(grid.free.shape)
    for row in rows:
        if row['geometry'] != 'valid' or row['terminal_trials'] <= 0:
            continue
        distance = distance_field(grid,row['xy'],min(3*sigma,max_support_distance_m))
        weight = np.exp(-.5*(distance/sigma)**2)
        numerator += weight*row['successes']; denominator += weight*row['terminal_trials']
    values = np.full(grid.free.shape,np.nan)
    observed = grid.free & (denominator > 1e-12)
    values[observed] = numerator[observed]/denominator[observed]
    return values,denominator


def export_heatmap(path, grid, rows, anchor, sigma, *, selection=None, title='', max_support_distance_m=.35):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    path = Path(path); path.mkdir(parents=True,exist_ok=True)
    values,evidence = gaussian_map(grid,rows,sigma,max_support_distance_m=max_support_distance_m)
    np.savez_compressed(path/'success_heatmap.npz',origin=grid.origin,resolution_m=grid.resolution,
                        free=grid.free,success_rate=values,weighted_trials=evidence,
                        sigma_m=sigma,max_support_distance_m=min(3*sigma,max_support_distance_m))
    height,width = grid.free.shape
    extent = [grid.origin[0]-.5*grid.resolution,grid.origin[0]+(width-.5)*grid.resolution,
              grid.origin[1]-.5*grid.resolution,grid.origin[1]+(height-.5)*grid.resolution]
    fig,ax = plt.subplots(figsize=(8,7),layout='constrained')
    ax.imshow(np.where(grid.free,np.nan,0.),origin='lower',extent=extent,cmap='Greys_r',vmin=0,vmax=1)
    im = ax.imshow(values,origin='lower',extent=extent,cmap='viridis',vmin=0,vmax=1)
    for obstacle in grid.support_obstacles:
        lo,hi = np.asarray(obstacle['min']),np.asarray(obstacle['max'])
        ax.add_patch(plt.Rectangle(lo[:2],*(hi-lo)[:2],facecolor='#eed7b2',
                                  edgecolor='#987d54',lw=1.5,zorder=3))
        ax.annotate('support / obstacle',(lo[:2]+hi[:2])/2,ha='center',fontsize=8,zorder=4)
    for row in rows:
        x,y = row['xy']
        if row['geometry'] != 'valid':
            ax.scatter(x,y,c='#777777',marker='x',s=15)
        elif row['success_rate'] is None:
            ax.scatter(x,y,facecolors='none',edgecolors='#777777',s=20)
        else:
            ax.scatter(x,y,c=[row['success_rate']],cmap='viridis',vmin=0,vmax=1,
                       edgecolors='white',linewidths=.6,s=30)
    ax.scatter(*anchor[:2],c='black',marker='*',s=140,label='target',zorder=6)
    if selection:
        for i,start in enumerate(selection.get('starts',[])):
            ax.scatter(*start['xy'],facecolors='none',edgecolors='#d55e00',s=150,linewidths=2)
            ax.annotate('S0-'+str(i+1),start['xy'],xytext=(4,5),textcoords='offset points')
        for rollout in selection.get('successful',[]):
            points = np.asarray(rollout['path']['xy'])
            ax.plot(points[:,0],points[:,1],c='#0072b2',linewidth=1.5)
            ax.scatter(*rollout['goal']['xy'],marker='s',facecolors='none',edgecolors='#0072b2',s=100)
    ax.set(xlabel='world X (m)',ylabel='world Y (m)',title=title or 'Observed operation success rate')
    ax.set_aspect('equal'); fig.colorbar(im,ax=ax,label='Success rate (0–1)')
    ax.text(.01,.01,'dots: measured trials; field: Gaussian smoothing\ngray ×: filtered; white: unobserved; dark: obstacle / outside floor',
            transform=ax.transAxes,fontsize=8,bbox={'facecolor':'white','alpha':.85,'edgecolor':'none'})
    fig.savefig(path/'success_heatmap.png',dpi=150); fig.savefig(path/'success_heatmap.svg'); plt.close(fig)
    write_json(path/'heatmap.json',{'sigma_m':sigma,'configured_support_distance_m':max_support_distance_m,
               'max_support_distance_m':min(3*sigma,max_support_distance_m),
               'support_obstacles':list(grid.support_obstacles),'kernel':'Gaussian of obstacle-aware grid shortest-path distance',
               'overlap':'sum all sample weights, then normalize; no overwrite',
               'obstacle_mask':'mask blocked cells after interpolation; not navigation reachability',
               'rate':'weighted successes / weighted terminal trials','observation_unit':'station trial',
               'unknown_is_not_zero':True,'physical_navigation_validation':'separate from conservative raster',
               'map':'success_heatmap.npz','png':'success_heatmap.png','svg':'success_heatmap.svg'})
    # Self-contained reading surface verifies the intended map embedding offline.
    import base64,html
    encoded = base64.b64encode((path/'success_heatmap.png').read_bytes()).decode()
    nav_image = path/'navigation_grid.png'
    nav_html = ''
    if nav_image.is_file():
        nav_encoded = base64.b64encode(nav_image.read_bytes()).decode()
        nav_html = '<h2>A* obstacles before execution</h2><img style="max-width:100%" alt="A star navigation obstacles" src="data:image/png;base64,'+nav_encoded+'">'
    (path/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Success map</title>'
        + '<h1>'+html.escape(title)+'</h1><img style="max-width:100%" alt="measured station success heatmap" '
        + 'src="data:image/png;base64,'+encoded+'"><p>Ground-plane map. Colors are Gaussian-smoothed observed trials, '
        + 'not additional measurements. Filtered and untested points are not counted as failures.</p>'+nav_html,encoding='utf-8')
    return path/'success_heatmap.png'
