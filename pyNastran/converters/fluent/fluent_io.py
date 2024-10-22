"""Defines the GUI IO file for Fluent."""
from __future__ import annotations
from typing import Any, TYPE_CHECKING
import numpy as np

import vtkmodules
from pyNastran.gui.vtk_interface import vtkTriangle, vtkQuad

from pyNastran.utils.convert import convert_pressure, convert_length
from pyNastran.converters.fluent.fluent import read_fluent, Fluent
from pyNastran.gui.gui_objects.gui_result import GuiResult, NormalResult
from pyNastran.gui.utils.vtk.vtk_utils import (
    create_vtk_cells_of_constant_element_type, numpy_to_vtk_points)
from pyNastran.gui.utils.vtk.vectorized_geometry import (
    create_offset_arrays, build_vtk_geometry)
if TYPE_CHECKING:
    from pyNastran.gui.main_window import MainWindow
    from vtk import vtkUnstructuredGrid

class FluentIO:
    def __init__(self, gui: MainWindow):
        self.gui = gui

    def get_fluent_wildcard_geometry_results_functions(self):
        data = ('Fluent',
                'Fluent Field (*.cel)', self.load_fluent_geometry,
                None, None)
        return data

    def load_fluent_geometry(self, fld_filename: str,
                             name: str='main', plot: bool=True):
        gui = self.gui
        model_name = name
        skip_reading = gui._remove_old_geometry(fld_filename)
        if skip_reading:
            return

        log = gui.log
        model = read_fluent(
            fld_filename, #auto_read_write_h5=False,
            log=log, debug=False)
        #self.model_type = model.model_type

        node_id = model.node_id
        nodes = model.xyz
        nnodes = len(nodes)

        # support multiple results
        titles = model.titles

        #regions_to_remove = [] #3
        #regions_to_include = [7, 4]
        #regions_to_include = []
        regions_to_remove = self.gui.settings.other_settings.cart3d_fluent_remove
        regions_to_include = self.gui.settings.other_settings.cart3d_fluent_include

        element_id, tris, quads, region, results = model.get_filtered_data(
            regions_to_remove, regions_to_include)
        out = model.get_area_centroid_normal(tris, quads)
        tri_area, quad_area, tri_centroid, quad_centroid, tri_normal, quad_normal = out
        normal = np.vstack([quad_normal, tri_normal])
        centroid = np.vstack([quad_centroid, tri_centroid])
        area = np.hstack([quad_area, tri_area])
        nnodes_array = np.hstack([quad_area*0+4, tri_area*0+3])

        if 1:
            nodes = convert_length(nodes, 'm', 'in')
            results[:, 0] = convert_pressure(results[:, 0], 'Pa', 'psi')

        nelement = len(element_id)
        assert len(element_id) == len(region), f'neids={len(element_id)} nregion={len(region)}'

        gui.nnodes = nnodes
        gui.nelements = nelement

        gui.log.info('nnodes=%s nelements=%s' % (gui.nnodes, gui.nelements))
        ugrid = self.gui.grid
        ugrid.Allocate(gui.nelements, 1000)

        assert nodes is not None
        points = numpy_to_vtk_points(nodes)
        ugrid.SetPoints(points)
        log.info(f'created vtk points')

        xmax, ymax, zmax = nodes.max(axis=0)
        xmin, ymin, zmin = nodes.min(axis=0)
        gui.log_info("xmin=%s xmax=%s dx=%s" % (xmin, xmax, xmax-xmin))
        gui.log_info("ymin=%s ymax=%s dy=%s" % (ymin, ymax, ymax-ymin))
        gui.log_info("zmin=%s zmax=%s dz=%s" % (zmin, zmax, zmax-zmin))
        dim_max = max(xmax-xmin, ymax-ymin, zmax-zmin)

        assert len(node_id) == len(np.unique(node_id))
        _create_elements(ugrid, node_id, tris, quads)
        log.info(f'created vtk elements')

        gui.nid_map = {}
        gui.create_global_axes(dim_max)

        ugrid.Modified()

        # loadSTLResults - regions/loads
        gui.scalar_bar_actor.VisibilityOff()
        gui.scalar_bar_actor.Modified()

        cases = {}
        gui.isubcase_name_map = {}
        ID = 1

        gui.isubcase_name_map[ID] = ('Fluent', '')
        form, cases = _fill_fluent_case(
            cases, ID, node_id, element_id,
            region, results, titles, normal,
            nnodes_array)

        gui.node_ids = node_id
        gui.element_ids = element_id
        log.debug(f'running _finish_results_io2')
        gui._finish_results_io2(model_name, form, cases)
        log.info(f'finished')

def _create_elements(ugrid: vtkUnstructuredGrid,
                     node_id: np.ndarray,
                     tris: np.ndarray,
                     quads: np.ndarray) -> None:
    cell_type_list = []
    cell_offset_list = []

    nquad = len(quads)
    ntri = len(tris)
    nelement_total = nquad + ntri
    cell_offset0 = 0
    n_nodes_list = []

    assert tris.shape[1] == 5, tris.shape
    assert quads.shape[1] == 6, quads.shape
    tri_nodes = tris[:, 2:]
    quad_nodes = quads[:, 2:]
    assert tri_nodes.shape[1] == 3, tri_nodes.shape
    assert quad_nodes.shape[1] == 4, quad_nodes.shape
    assert node_id.min() >= 1, node_id.min()
    all_nodes = np.unique(np.hstack([tri_nodes.ravel(), quad_nodes.ravel()]))
    assert all_nodes.min() >= 1, all_nodes.min()
    missing_nodes = np.setdiff1d(all_nodes, node_id)
    assert len(missing_nodes) == 0, missing_nodes
    if nquad:
        #elem.GetCellType() = 9  # vtkQuad
        cell_type = 9
        dnode = 4
        cell_offset0, n_nodesi, cell_typei, cell_offseti = create_offset_arrays(
            node_id, quad_nodes,
            nquad, cell_type, cell_offset0, dnode)
        n_nodes_list.append(n_nodesi.ravel())
        cell_type_list.append(cell_typei)
        cell_offset_list.append(cell_offseti)

    if ntri:
        #elem.GetCellType() = 5  # vtkTriangle
        cell_type = 5
        dnode = 3
        cell_offset0, n_nodesi, cell_typei, cell_offseti = create_offset_arrays(
            node_id, tri_nodes,
            ntri, cell_type, cell_offset0, dnode)
        n_nodes_list.append(n_nodesi.ravel())
        cell_type_list.append(cell_typei)
        cell_offset_list.append(cell_offseti)

    n_nodes = np.hstack(n_nodes_list)
    cell_type = np.hstack(cell_type_list)
    cell_offset = np.hstack(cell_offset_list)
    build_vtk_geometry(
        nelement_total, ugrid,
        n_nodes, cell_type, cell_offset)
    return

def _fill_fluent_case(cases: dict[int, Any],
                      ID: int,
                      node_ids: np.ndarray,
                      element_ids: np.ndarray,
                      region: np.ndarray,
                      results: np.ndarray,
                      titles: np.ndarray,
                      normal: np.ndarray,
                      nnodes_array: np.ndarray,) -> None:
    """adds the sidebar results"""
    # reorg the ids
    #element_ids = np.unique(np.hstack([tris[:, 0], quads[:, 0]]))
    #colormap = 'jet'
    icase = 0
    itime = 0
    colormap = 'jet'

    nid_res = GuiResult(ID, header='NodeID', title='NodeID',
                        location='node', scalar=node_ids)
    eid_res = GuiResult(ID, header='ElementID', title='ElementID',
                        location='centroid', scalar=element_ids)
    region_res = GuiResult(ID, header='Region', title='Region',
                           location='centroid', scalar=region)

    nxyz_res = NormalResult(0, 'Normals', 'Normals',
                            nlabels=2, labelsize=5, ncolors=2,
                            #colormap=colormap,
                            data_format='%.1f',
                            uname='NormalResult')
    nx_res = GuiResult(ID, 'normal_x', 'NormalX', 'centroid', normal[:, 0],
                       data_format='%.3f', colormap=colormap, uname='NormalX')
    ny_res = GuiResult(ID, 'normal_y', 'NormalY', 'centroid', normal[:, 1],
                       data_format='%.3f', colormap=colormap, uname='NormalY')
    nz_res = GuiResult(ID, 'normal_z', 'NormalZ', 'centroid', normal[:, 2],
                       data_format='%.3f', colormap=colormap, uname='NormalZ')

    nnodes_res = GuiResult(ID, 'nnodes', 'Nnodes', 'centroid', nnodes_array,
                       data_format='%.0f', colormap=colormap, uname='Nnodes')

    assert len(element_ids) == len(region), f'neids={len(element_ids)} nregion={len(region)}'
    cases[icase] = (nid_res, (itime, 'NodeID'))
    cases[icase + 1] = (eid_res, (itime, 'ElementID'))
    cases[icase + 2] = (region_res, (itime, 'Region'))

    cases[icase + 3] = (nxyz_res, (itime, 'Normal'))
    cases[icase + 4] = (nx_res, (itime, 'NormalX'))
    cases[icase + 5] = (ny_res, (itime, 'NormalY'))
    cases[icase + 6] = (nz_res, (itime, 'NormalZ'))
    cases[icase + 7] = (nnodes_res, (itime, 'Nnodes'))

    form = [
        ('NodeID', icase, []),
        ('ElementID', icase + 1, []),
        ('Region', icase + 2, []),

        ('Normal', icase + 3, []),
        ('NormalX', icase + 4, []),
        ('NormalY', icase + 5, []),
        ('NormalZ', icase + 6, []),
        ('Nnodes', icase + 7, []),
    ]
    icase += 8

    for i, title in enumerate(titles[1:]):
        result = results[:, i]
        assert len(element_ids) == len(result)
        pressure_res = GuiResult(ID, header=title, title=title,
                                 location='centroid', scalar=result)
        cases[icase] = (pressure_res, (itime, title))
        formi = (title, icase, [])
        form.append(formi)
        icase += 1
    return form, cases
