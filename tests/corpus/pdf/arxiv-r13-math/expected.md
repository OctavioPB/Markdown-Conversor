# DRAFT — requiere revisión humana (transcrito a mano de la fuente: source.txt y la página)

# WELL-POSEDNESS OF THE LINEAR REGULARIZED 13-MOMENT EQUATIONS USING TENSOR-VALUED KORN INEQUALITIES

PETER LEWINTAN[^1], LAMBERT THEISEN[^2], AND MANUEL TORRILHON[^3]

**Abstract.** In this paper, we finally prove the well-posedness of the linearized R13 moment model, which describes, e.g., rarefied gas flows. As an extension of the classical fluid equations, moment models are robust and have been frequently used, yet they are challenging to analyze due to their additional equations. By effectively grouping variables, we identify a 2-by-2 block structure, allowing us to analyze well-posedness within the abstract LBB framework for saddle point problems. Due to the unique tensorial structure of the equations, in addition to an interesting combination of tools from Stokes’ and linear elasticity theory, we also need new coercivity estimates for tensor fields. These Korn-type inequalities are established by analyzing the symbol map of the symmetric and trace-free part of tensor derivative fields. Together with the corresponding right inverse of the tensorial divergence, we obtain the existence and uniqueness of weak solutions. This result also serves as the basis for future numerical analysis of corresponding discretization schemes.

**Key words.** regularized 13-moment equations, well-posedness, Korn inequalities, coercivity estimates, ellipticity, saddle point problem

**MSC codes.** 76P05, 65N30, 26D10, 35Q35, 35A23, 65K10, 35A01

**DOI.** Preprint

## 1. Introduction

In this paper, we propose and analyze a mixed formulation for the linear regularized 13-moment equations (R13) \[55\] on bounded Lipschitz domains Ω ⊂ R³ with boundary Γ := ∂Ω, i.e., Ω is an open, non-empty, connected set whose boundary Γ can be locally expressed as the graph of a Lipschitz continuous function. The R13 equations are a system of partial differential equations that describe the evolution of the macroscopic quantities in, e.g., rarefied gases or microfluids. In these flows, the length of the mean free path between particle collisions becomes significant when compared to a reference length scale of the process. The ratio between these length scales is typically given by the *Knudsen number* Kn > 0, which serves as a parameter in the equations. Let m : Ω → R, b : Ω → R³, and r : Ω → R be a mass source, a body force, and an energy source. Similar to the Stokes equations combined with Fourier’s law of heat conduction, we are interested in the fields for the pressure p : Ω → R, the velocity u : Ω → R³, and the temperature θ : Ω → R, given by

(1.1) div u = m, ∇p + Div σ = b, div u + div s = r, in Ω,

where we distinguish between div u : Ω → R, div u := Σⱼ ∂ⱼuⱼ, and Div σ : Ω → R³, (Div σ)ᵢ := Σⱼ ∂ⱼσᵢⱼ, acting on vectors and matrices (row-wise), respectively. A

This preprint reflects the status as of April 27, 2026 and is subject to change. Modifications resulting from the publishing process, such as peer review, editing, and formatting, may not be incorporated here. Please refer to the final published version for the most accurate content. **Funding:** Supported by the German Research Foundation (DFG), project 442047500 (LT/MT).

[^1]: Institute for Analysis, Karlsruhe Institute of Technology, Englerstr. 2, 76131 Karlsruhe, Germany (peter.lewintan@kit.edu)

[^2]: Corresponding author. Applied and Computational Mathematics, RWTH Aachen University, Schinkelstr. 2, 52062 Aachen, Germany (lambert.theisen@rwth-aachen.de, https://thsn.dev)

[^3]: Applied and Computational Mathematics, RWTH Aachen University, Schinkelstr. 2, 52062 Aachen, Germany (mt@acom.rwth-aachen.de)
