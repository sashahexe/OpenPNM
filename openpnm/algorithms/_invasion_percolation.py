import logging
import heapq as hq
import numpy as np
from tqdm import tqdm
from scipy.stats import rankdata
from collections import namedtuple
from openpnm.utils import Docorator
from openpnm.topotools import find_clusters
from openpnm.algorithms import GenericAlgorithm
from openpnm._skgraph.simulations import (
    bond_percolation,
    find_connected_clusters,
    find_trapped_sites,
    find_trapped_bonds
)


__all__ = ['InvasionPercolation']


logger = logging.getLogger(__name__)
docstr = Docorator()


@docstr.get_sections(base='IPSettings',
                     sections=['Parameters', 'Other Parameters'])
@docstr.dedent
class IPSettings:
    r"""

    Parameters
    ----------
    %(GenericAlgorithmSettings.parameters)s
    pore_volume : str
        The dictionary key for the pore volume array
    throat_volume : str
        The dictionary key for the throat volume array
    entry_pressure : str
        The dictionary key for the throat capillary pressure

    """
    phase = ''
    pore_volume = 'pore.volume'
    throat_volume = 'throat.volume'
    entry_pressure = 'throat.entry_pressure'


class InvasionPercolation(GenericAlgorithm):
    r"""
    A classic invasion percolation algorithm optimized for speed with numba

    Parameters
    ----------
    network : GenericNetwork
        The Network upon which the invasion will occur

    Notes
    -----
    This algorithm uses a `binary heap <https://en.wikipedia.org/wiki/Binary_heap>`_
    to store a list of all accessible throats, sorted according to entry
    pressure.  This means that item [0] in the heap is the most easily invaded
    throat that is currently accessible by the invading fluid, so looking up
    which throat to invade next is computationally trivial. In order to keep
    the list sorted, adding new throats to the list takes more time; however,
    the heap data structure is very efficient at this.

    """

    def __init__(self, phase, name='ip_#', **kwargs):
        super().__init__(name=name, **kwargs)
        self.settings._update(IPSettings())
        self.settings['phase'] = phase.name
        self['pore.inlets'] = False
        self['pore.outlets'] = False
        self.reset()

    def reset(self):
        self['pore.invasion_sequence'] = -1
        self['throat.invasion_sequence'] = -1
        self['pore.trapped'] = False
        self['throat.trapped'] = False
        self['pore.residual'] = False
        self['throat.residual'] = False
        self.queue = []

    def set_residual(self, pores=None, throats=None, mode='add'):
        if mode == 'add':
            if pores is not None:
                self['pore.residual'][pores] = True
            if throats is not None:
                self['throat.residual'][throats] = True
        elif mode == 'drop':
            if pores is not None:
                self['pore.residual'][pores] = False
            if throats is not None:
                self['throat.residual'][throats] = False
        elif mode == 'clear':
            if pores is not None:
                self['pore.residual'] = False
            if throats is not None:
                self['throat.residual'] = False
        elif mode == 'overwrite':
            if pores is not None:
                self['pore.residual'] = False
                self['pore.residual'][pores] = True
            if throats is not None:
                self['throat.residual'] = False
                self['throat.residual'][throats] = True

    def set_inlets(self, pores=[], mode='add'):
        r"""
        Specifies from which pores the invasion process starts

        Parameters
        ----------
        pores : array_like
            The list of pores from which the invading phase can enter the network
        mode : str
            Controls how the given inlets are added.  Options are:

            ============ ======================================================
            mode         description
            ============ ======================================================
            'add'        Adds the given ``pores`` to list of inlets, while
                         keeping any already defined inlets
            'drop'       Removes given ``pores`` from list of inlets
            'clear'      Removes all currently specified inlets
            'overwrite'  Removes all existing inlets, then adds the given
                         ``pores``
            ============ =======================================================

        """
        if mode == 'add':
            self['pore.inlets'][pores] = True
        elif mode == 'drop':
            self['pore.inlets'][pores] = False
        elif mode == 'clear':
            self['pore.inlets'] = False
        elif mode == 'overwrite':
            self['pore.inlets'] = False
            self['pore.inlets'][pores] = True
        else:
            raise Exception(f'Unrecognized mode {mode}')

    def set_outlets(self, pores=[], mode='add'):
        r"""
        Specifies from which pores the defending fluid exits the domain

        Parameters
        ----------
        pores : array_like
            The list of pores from which the defending fluid exits
        mode : str
            Controls how the given inlets are added.  Options are:

            ============ ======================================================
            mode         description
            ============ ======================================================
            'add'        Adds the given ``pores`` to list of outlets, while
                         keeping any already defined outlets
            'drop'       Removes given ``pores`` from list of outlets
            'clear'      Removes all currently specified outlets
            'overwrite'  Removes all existing outlets, then adds the given
                         ``pores``
            ============ =======================================================

        """
        if mode == 'add':
            self['pore.outlets'][pores] = True
        elif mode == 'drop':
            self['pore.outlets'][pores] = False
        elif mode == 'clear':
            self['pore.outlets'] = False
        elif mode == 'overwrite':
            self['pore.outlets'] = False
            self['pore.outlets'][pores] = True
        else:
            raise Exception(f'Unrecognized mode {mode}')

    def run(self, n_steps=None):
        r"""
        Performs the algorithm for the given number of steps

        Parameters
        ----------
        n_steps : int
            The number of throats to invade during the run.  This can be
            used to incrementally invading the network, allowing for
            simulations to occur between each call to ``run``. If ``None``
            (default) then the entire network is invaded.

        """

        # Setup arrays and info
        # TODO: This should be called conditionally so that it doesn't
        # overwrite existing data when doing a few steps at a time
        self._run_setup()

        if n_steps is None:
            n_steps = np.inf

        if len(self.queue) == 0:
            logger.warn('queue is empty, this network is fully invaded')
            return

        # Create incidence matrix for use in _run_accelerated which is jit
        incidence_matrix = self.network.create_incidence_matrix(fmt='csr')
        t_inv, p_inv, p_inv_t = InvasionPercolation._run_accelerated(
            queue=self.queue,
            t_sorted=self['throat.sorted'],
            t_order=self['throat.order'],
            t_inv=self['throat.invasion_sequence'],
            p_inv=self['pore.invasion_sequence'],
            p_inv_t=np.zeros_like(self['pore.invasion_sequence']),
            conns=self.project.network['throat.conns'],
            idx=incidence_matrix.indices,
            indptr=incidence_matrix.indptr,
            n_steps=n_steps
        )

        self['throat.invasion_sequence'] = t_inv
        self['pore.invasion_sequence'] = p_inv
        self['throat.invasion_pressure'] = self['throat.entry_pressure']
        self['pore.invasion_pressure'] = self['throat.entry_pressure'][p_inv_t]
        # Set invasion pressure of inlets to 0
        self['pore.invasion_pressure'][self['pore.invasion_sequence'] == 0] = 0.0
        # Set invasion sequence and pressure of any residual pores/throats to 0
        self['throat.invasion_sequence'][self['throat.residual']] = 0
        self['pore.invasion_sequence'][self['pore.residual']] = 0
        self['pore.invasion_pressure'][self['pore.residual']] = 0

    def _run_setup(self):
        self['pore.invasion_sequence'][self['pore.inlets']] = 0
        # self['pore.invasion_sequence'][self['pore.residual']] = 0
        # self['throat.invasion_sequence'][self['throat.residual']] = 0
        # Set throats between inlets as trapped
        Ts = self.network.find_neighbor_throats(self['pore.inlets'], mode='xnor')
        self['throat.trapped'][Ts] = True
        # Get throat capillary pressures from phase and update
        phase = self.project[self.settings['phase']]
        self['throat.entry_pressure'] = phase[self.settings['entry_pressure']]
        self['throat.entry_pressure'][self['throat.residual']] = 0.0
        # Generated indices into t_entry giving a sorted list
        self['throat.sorted'] = np.argsort(self['throat.entry_pressure'], axis=0)
        self['throat.order'] = 0
        self['throat.order'][self['throat.sorted']] = np.arange(0, self.Nt)
        # Perform initial analysis on input pores
        pores = self['pore.inlets']
        Ts = self.project.network.find_neighbor_throats(pores=pores)
        for T in self['throat.order'][Ts]:
            hq.heappush(self.queue, T)

    def _run_accelerated(queue, t_sorted, t_order, t_inv, p_inv, p_inv_t,
                         conns, idx, indptr, n_steps):
        r"""
        Numba-jitted run method for InvasionPercolation class.

        Notes
        -----
        ``idx`` and ``indptr`` are properties are the network's incidence
        matrix, and are used to quickly find neighbor throats.

        Numba doesn't like foreign data types (i.e. GenericNetwork), and so
        ``find_neighbor_throats`` method cannot be called in a jitted method.

        Nested wrapper is for performance issues (reduced OpenPNM import)
        time due to local numba import

        """
        from numba import njit

        @njit
        def wrapper(queue, t_sorted, t_order, t_inv, p_inv, p_inv_t, conns,
                    idx, indptr, n_steps):
            count = 1
            while (len(queue) > 0) and (count < (n_steps + 1)):
                # Find throat at the top of the queue
                t = hq.heappop(queue)
                # Extract actual throat number
                t_next = t_sorted[t]
                t_inv[t_next] = count
                # If throat is duplicated
                while len(queue) > 0 and queue[0] == t:
                    _ = hq.heappop(queue)
                # Find pores connected to newly invaded throat
                Ps = conns[t_next]
                # Remove already invaded pores from Ps
                Ps = Ps[p_inv[Ps] < 0]
                if len(Ps) > 0:
                    p_inv[Ps] = count
                    p_inv_t[Ps] = t_next
                    for i in Ps:
                        Ts = idx[indptr[i]:indptr[i+1]]
                        Ts = Ts[t_inv[Ts] < 0]
                    for i in np.unique(Ts):  # Exclude repeated neighbor throats
                        hq.heappush(queue, t_order[i])
                count += 1
            return t_inv, p_inv, p_inv_t

        return wrapper(queue, t_sorted, t_order, t_inv, p_inv, p_inv_t, conns,
                       idx, indptr, n_steps)

    def apply_trapping(self, n_steps=10):
        r"""
        Analyze which pores and throats are trapped

        Parameters
        ----------
        n_steps: int
            The number of steps between between evaluations of trapping.
            A value of 1 will provide the "True" result, but this
            would require long computational time since a network
            clustering is performed for each step. The default is 10, which
            will incur some error (pores and throats are identified as invaded
            that are actually trapped), but is a good compromise.

        Returns
        -------
        This function does not return anything.  It adjusts the
        ``'pore.invasion_sequence'`` and ``'throat.invasion_sequence'`` arrays
        on the object by setting trapped pores/throats to -1, and adjusting
        the sequence values to be contiguous.  It also puts ``True`` values
        into the ``'pore.trapped'`` and ``'throat.trapped'`` arrays

        Notes
        -----
        Outlet pores must be specified (using ``set_outlets`` or putting
        ``True`` values in ``alg['pore.outlets']``) or else an exception is
        raised.

        """
        if not np.any(self['pore.outlets']):
            raise Exception('pore outlets must be specified first')
        # Firstly, any pores/throats with inv_seq > outlets are trapped
        # N = self['pore.invasion_sequence'][self['pore.outlets']].max()
        # self['pore.trapped'][self['pore.invasion_sequence'] > N] = True
        # self['throat.trapped'][self['throat.invasion_sequence'] > N] = True
        # Now scan network and find pores/throats disconnected from outlets
        msg = 'Evaluating trapping'
        # TODO: This could be parallelized with dask since each loop is
        # independent of the others
        N = self['pore.invasion_sequence'].max()
        for i in tqdm(range(0, int(N), n_steps), msg):
            tmask = self['throat.invasion_sequence'] < i
            s, b = find_trapped_bonds(conns=self.network.conns,
                                      occupied_bonds=tmask,
                                      outlets=self['pore.outlets'])
            P_trapped = s >= 0
            T_trapped = b >= 0
            self['pore.trapped'] += P_trapped
            self['throat.trapped'] += T_trapped
        # Set any residual pores within trapped clusters back to untrapped
        self['pore.trapped'][self['pore.residual']] = False
        self['throat.trapped'][self['throat.residual']] = False
        # Set trapped pores/throats to uninvaded and adjust invasion sequence
        self['pore.invasion_sequence'][self['pore.trapped']] = -1
        self['throat.invasion_sequence'][self['throat.trapped']] = -1
        # The -2 is to shift uninvaded pores to -1 and initially invaded to 0
        # self['pore.invasion_sequence'] = \
        #     rankdata(self['pore.invasion_sequence'], method='dense') - 2
        # self['throat.invasion_sequence'] = \
        #     rankdata(self['throat.invasion_sequence'], method='dense') - 2

    def pc_curve(self):
        r"""
        Get the percolation data as the invader volume vs the capillary
        pressure.

        """
        net = self.project.network
        pvols = net[self.settings['pore_volume']]
        tvols = net[self.settings['throat_volume']]
        tot_vol = np.sum(pvols) + np.sum(tvols)
        # Normalize
        pvols /= tot_vol
        tvols /= tot_vol
        # Remove trapped volume
        pmask = self['pore.invasion_sequence'] > -1
        tmask = self['throat.invasion_sequence'] > -1
        pvols = pvols[pmask]
        tvols = tvols[tmask]
        pseq = self['pore.invasion_sequence'][pmask]
        tseq = self['throat.invasion_sequence'][tmask]
        pPc = self['pore.invasion_pressure'][pmask]
        tPc = self['throat.invasion_pressure'][tmask]
        vols = np.concatenate((pvols, tvols))
        seqs = np.concatenate((pseq, tseq))
        Pcs = np.concatenate((pPc, tPc))
        data = np.rec.fromarrays([seqs, vols, Pcs],
                                 formats=['i', 'f', 'f'],
                                 names=['seq', 'vol', 'Pc'])
        data.sort(axis=0, order='seq')
        pc_curve = namedtuple('pc_curve', ('pc', 'snwp'))
        data = pc_curve(data.Pc, np.cumsum(data.vol))
        return data


if __name__ == '__main__':
    import openpnm as op
    import matplotlib.pyplot as plt

    np.random.seed(0)
    Nx, Ny, Nz = 25, 25, 1
    pn = op.network.Cubic(shape=[Nx, Ny, Nz], spacing=1e-4)
    pn.add_model_collection(op.models.collections.geometry.spheres_and_cylinders)
    pn.regenerate_models()
    pn['pore.volume@left'] = 0.0

    water = op.phase.Water(network=pn, name='h2o')
    water.add_model_collection(op.models.collections.physics.standard)
    water.regenerate_models()

    p_residual = np.random.randint(0, Nx*Ny, int(Nx*Ny/10))
    t_residual = pn.find_neighbor_throats(p_residual, mode='or')
    # %%
    ip = InvasionPercolation(network=pn, phase=water)
    ip.set_inlets(pn.pores('left'))
    ip.set_residual(pores=p_residual, throats=t_residual)
    ip.run()
    ip.set_outlets(pn.pores('right'))
    ip.apply_trapping(n_steps=1)

    # %%
    drn = op.algorithms.Drainage(network=pn, phase=water)
    drn.set_inlets(pn.pores('left'))
    drn.set_residual(pores=p_residual, throats=t_residual)
    pressures = np.unique(ip['pore.invasion_pressure'])
    # pressures = np.logspace(np.log10(0.1e3), np.log10(2e4), 100)
    drn.run(pressures=pressures)
    drn.set_outlets(pn.pores('right'))
    drn.apply_trapping(pressures=None)

    # %%
    fig, ax = plt.subplots(1, 1)
    ax.step(*ip.pc_curve(), 'b', where='post')
    ax.step(*drn.pc_curve(), 'r--', where='post')
    ax.set_ylim([0, 1])

    # %%
    if 1:
        fig, ax = plt.subplots(1, 1)
        ax.set_facecolor('grey')
        # ax = op.topotools.plot_coordinates(network=pn, c='w', ax=ax)
        tmask = ip['throat.invasion_sequence'] >= 0
        ax = op.topotools.plot_connections(network=pn,
                                           throats=tmask,
                                           color_by=ip['throat.invasion_sequence'],
                                           ax=ax)
        ax = op.topotools.plot_coordinates(network=pn, pores=ip['pore.residual'], c='w', s=200, ax=ax)























