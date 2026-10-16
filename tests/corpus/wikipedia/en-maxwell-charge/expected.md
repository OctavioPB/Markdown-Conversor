# DRAFT — requiere revisión humana (borrador de Pandoc adaptado al dialecto)

# Maxwell's equations

## Charge conservation

The invariance of charge can be derived as a corollary of Maxwell's equations. The left-hand side of the Ampère–Maxwell law has zero divergence by the [div–curl identity](https://en.wikipedia.org/wiki/Vector_calculus_identities#Divergence_of_curl_is_zero). Expanding the divergence of the right-hand side, interchanging derivatives, and applying Gauss's law gives:

$$0 = \nabla\cdot (\nabla\times \mathbf{B}) = \nabla \cdot \left(\mu_0 \left(\mathbf{J} + \varepsilon_0 \frac{\partial \mathbf{E}} {\partial t} \right) \right) = \mu_0\left(\nabla\cdot \mathbf{J} + \varepsilon_0\frac{\partial}{\partial t}\nabla\cdot \mathbf{E}\right) = \mu_0\left(\nabla\cdot \mathbf{J} +\frac{\partial \rho}{\partial t}\right)

$$

i.e.,

$$

\frac{\partial \rho}{\partial t} + \nabla \cdot \mathbf{J} = 0.$$

By the Gauss divergence theorem, this means the rate of change of charge in a fixed volume equals the net current flowing through the boundary:

$\frac{d}{dt}Q_\Omega = \frac{d}{dt} \iiint_{\Omega} \rho \mathrm{d}V = -$ $\partial \Omega$ $\mathbf{J} \cdot {\rm d}\mathbf{S} = - I_{\partial \Omega}.$

In particular, in an isolated system the total charge is conserved.
