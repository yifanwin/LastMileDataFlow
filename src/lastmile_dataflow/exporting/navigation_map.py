"""Pre-execution A* raster: physical support versus inflated chassis exclusion."""
from pathlib import Path
import numpy as np


def export_navigation_map(path, grid, anchor, *, display_grid=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    path=Path(path);path.mkdir(parents=True,exist_ok=True)
    raster=display_grid if display_grid is not None else grid
    if raster.free.shape != grid.free.shape or raster.resolution != grid.resolution or not np.array_equal(raster.origin,grid.origin):
        raise ValueError('navigation display grid must match planner coordinates')
    h,w=raster.free.shape
    extent=[grid.origin[0]-.5*grid.resolution,grid.origin[0]+(w-.5)*grid.resolution,
            grid.origin[1]-.5*grid.resolution,grid.origin[1]+(h-.5)*grid.resolution]
    fig,ax=plt.subplots(figsize=(8,7),layout='constrained')
    ax.imshow(raster.free,origin='lower',extent=extent,cmap='Greys_r',vmin=0,vmax=1)
    for obstacle in grid.support_obstacles:
        lo,hi=np.asarray(obstacle['min']),np.asarray(obstacle['max'])
        ax.add_patch(plt.Rectangle(lo[:2],*(hi-lo)[:2],facecolor='#eed7b2',edgecolor='#987d54',lw=1.5))
    ax.scatter(*anchor[:2],marker='*',s=140,c='#e87521',label='target',zorder=5)
    ax.legend(handles=[Patch(facecolor='#eed7b2',edgecolor='#987d54',label='Furniture footprint'),
                       Patch(facecolor='black',label='Physical projection / outside floor' if display_grid is not None else 'Planning exclusion / outside floor'),
                       Patch(facecolor='white',edgecolor='#777',label='Floor display (planning clearance not shown)' if display_grid is not None else 'Conservative A* free cells')],fontsize=8)
    ax.set(xlabel='world X (m)',ylabel='world Y (m)',title='A* route map: physical footprints' if display_grid is not None else 'A* planning clearance')
    ax.set_aspect('equal')
    for ext in ('png','svg'):fig.savefig(path/('navigation_grid.'+ext),dpi=150)
    plt.close(fig)

    from ..io import write_json
    write_json(path/'navigation_display.json',{'display':'physical_footprints' if display_grid is not None else 'planning_clearance',
               'planner_grid_unchanged':True,'footprint_labels':False})
