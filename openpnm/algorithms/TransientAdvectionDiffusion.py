from openpnm.algorithms import TransientReactiveTransport, AdvectionDiffusion
from openpnm.utils import logging, Docorator, SettingsAttr
docstr = Docorator()
logger = logging.getLogger(__name__)


@docstr.dedent
class TransientAdvectionDiffusionSettings:
    r"""
    Parameters
    ----------
    %(AdvectionDiffusionSettings.parameters)s

    Other Parameters
    ----------------
    %(AdvectionDiffusionSettings.other_parameters)s

    ----

    **The following parameters pertain to the TransientReactiveTransport class**

    %(TransientReactiveTransportSettings.other_parameters)s

    ----

    **The following parameters pertain to the ReactiveTransport class**

    %(ReactiveTransportSettings.other_parameters)s

    ----

    **The following parameters pertain to the GenericTransport class**

    %(GenericTransportSettings.other_parameters)s
    """
    prefix = 'trans_ad'


class TransientAdvectionDiffusion(TransientReactiveTransport,
                                  AdvectionDiffusion):
    r"""
    A subclass of GenericTransport to perform steady and transient simulations
    of pure diffusion and advection-diffusion problems.

    """

    def __init__(self, phase, settings={}, **kwargs):
        self.settings = SettingsAttr(TransientAdvectionDiffusionSettings, settings)
        super().__init__(settings=self.settings, **kwargs)
        self.settings['phase'] = phase.name
