import scipy as sp
import numpy as np
import openpnm as op
import matplotlib.pyplot as plt

np.random.seed(10)

# %% Test scipy's solve_ivp
# FOR EXAMPLE: solve dy / dt = y^2 + y

def fun(t, y):
    
    return y

sol = sp.integrate.solve_ivp(fun, t_span=(0, 10), y0=np.array([5]))

# plot
t = sol.t
y = np.reshape(sol.y, newshape=len(sol.t))
plt.plot(t, y)

# %% Set up for the solvers
Nx = 10
shape = [Nx, Nx, 1]
spacing = 1/Nx
net = op.network.Cubic(shape=shape, spacing=spacing)
geo = op.geometry.SpheresAndCylinders(network=net, pores=net.Ps, throats=net.Ts)
air = op.phases.Air(network=net)
phys = op.physics.GenericPhysics(network=net, phase=air, geometry=geo)

# make diffusivity afunction of temperature - ALREADY IS!!
'''
air.add_model(propname='pore.diffusivity',
              model=op.models.misc.linear, 
              m=7.573435311355311e-08,
              b=0,
              prop='pore.temperature')
'''
phys.add_model(propname='throat.diffusive_conductance', 
               model=op.models.physics.diffusive_conductance.generic_diffusive)

air.remove_model(propname='pore.thermal_conductivity')
air.add_model(propname='pore.thermal_conductivity',
              model=op.models.misc.constant,
              value=2.5,
              regen_mode='constant')


phys.add_model(propname='throat.thermal_conductance',
               model=op.models.physics.thermal_conductance.generic_thermal)

tfd_settings = {
    "conductance": "throat.diffusive_conductance",
    "quantity": "pore.concentration",
    "cache_A": False,
    "cache_b": False  
}

tfc_settings = {
    "conductance": "throat.thermal_conductance",
    "quantity": "pore.temperature",
    "pore_volume": "pore.heat_capacity",
    "cache_A": False,
    "cache_b": False  
}

pardiso = op.solvers.PardisoSpsolve()
rk45 = op.integrators.ScipyRK45(verbose=True)

# %% Test multi-physics solver
# First algorithm, transient fourier conduction
tfc = op.algorithms.TransientReactiveTransport(network=net, phase=air)
geo['pore.heat_capacity'] = geo['pore.volume'] * 1.0035 * 1000 * 1.225
tfc.settings.update(tfc_settings)
tfc.set_value_BC(net.pores("left"), 400)
T0 = np.ones(tfc.Np) * 300

# Second algorithm, transient fickian diffusion
tfd = op.algorithms.TransientReactiveTransport(network=net, phase=air)
# tfd.settings['variable_props'] = 'pore.temperature'
tfd.settings.update(tfd_settings)
tfd.set_value_BC(net.pores("left"), 100)
# tfd.set_value_BC(net.pores("right"), 100)
c0 = np.zeros(tfd.Np)
'''
tspan = [0, 1000]
tout = np.linspace(tspan[0], tspan[1])
sol = tfc.run(x0=T0, tspan=tspan, integrator=rk45, saveat=tout)
'''
# manually solve multiphysics system
t_initial = 10
t_final = 110
t_step = 10
t_prev = 0
for i in range(t_initial, t_final, t_step):
    print('time:', i, "s")
    tspan = [t_prev, i]
    t_prev = i
    tout = i
    sol_1 = tfc.run(x0=T0, tspan=tspan, integrator=rk45, saveat=tout)
    air.regenerate_models() # update diffusivuty
    phys.regenerate_models() # update diffusive conductance because tfd does not have iterative props
    sol_2 = tfd.run(x0=c0, tspan=tspan, integrator=rk45, saveat=tout)
    # update initial coniditions
    T0 = sol_1[:, 1]
    c0 = sol_2[:, 1]

end_1 = sol_1[:, -1]
end_2 = sol_2[:, -1]

fig, ax = plt.subplots(ncols=2)
im_1 = ax[0].imshow(end_1.reshape((Nx,Nx)))
im_2 = ax[1].imshow(end_2.reshape((Nx,Nx)))
fig.colorbar(im_1, ax=ax[0], fraction=0.046, pad=0.04)
fig.colorbar(im_2, ax=ax[1], fraction=0.046, pad=0.04)
ax[0].title.set_text('Temperature (K)')
ax[1].title.set_text('Concentration (mol m-3)')
ax[0].get_xaxis().set_visible(False)
ax[1].get_xaxis().set_visible(False)
ax[0].get_yaxis().set_visible(False)
ax[1].get_yaxis().set_visible(False)
im_1.set_clim(300, 400)
im_2.set_clim(0, 100)