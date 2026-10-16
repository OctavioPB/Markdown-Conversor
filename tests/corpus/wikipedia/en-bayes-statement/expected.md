# DRAFT — requiere revisión humana (borrador de Pandoc adaptado al dialecto)

# Bayes' theorem

## Statement of theorem

Bayes' theorem is stated mathematically as the following equation:[^1]

$$
P(A\vert B) = \frac{P(B \vert A) P(A)}{P(B)}
$$

where $A$ and $B$ are [events](<https://en.wikipedia.org/wiki/Event_(probability_theory)>) and $P(B) \neq 0$.

- $P(A\vert B)$ is a [conditional probability](https://en.wikipedia.org/wiki/Conditional_probability): the probability of event $A$ occurring given that $B$ is true. It is also called the [posterior probability](https://en.wikipedia.org/wiki/Posterior_probability) of $A$ given $B$.
- $P(B\vert A)$ is also a conditional probability: the probability of event $B$ occurring given that $A$ is true. It can also be interpreted as the [likelihood function](https://en.wikipedia.org/wiki/Likelihood_function) evaluated at $A$ given a fixed $B$.
- $P(A)$ and $P(B)$ are the probabilities of observing $A$ and $B$ respectively without any given conditions. $P(A)$, the quantity of interest, is often called 'the prior probability' (prior to new evidence). Technically both $P(A)$ and $P(B)$ could be called prior, unconditioned, or [marginal probabilities](https://en.wikipedia.org/wiki/Marginal_probability).

*Visual proof of Bayes' theorem*

Bayes' theorem may be derived from the relation between [joint](https://en.wikipedia.org/wiki/Joint_probability_distribution) and conditional probabilities.[^2] The joint probability of the events $A$ and $B$ both happening, written $P(A \cap B)$, is equal to the conditional probability of $A$ given $B$ times the probability of $B$:

$$P(A \cap B) = P(A\vert B) P(B).

$$

Likewise,

$$

P(A \cap B) = P(B\vert A) P(A).

$$

The two products must therefore be equal to each other:

$$

P(A\vert B) P(B) = P(B\vert A) P(A),$$

and dividing both sides by $P(B)$ gives Bayes' theorem:

$$P(A\vert B) = \frac{P(B\vert A) P(A)}{P(B)}, \text{ if } P(B) \neq 0.

$$

If events *A*₁, *A*₂, ..., are mutually exclusive and exhaustive, i.e., one of them is certain to occur but no two can occur together, then, by the [law of total probability](https://en.wikipedia.org/wiki/Law_of_total_probability),

$$

P(B) = \sum_i P(B|A_i) P(A_i)$$

for any event *B.* Substituting this expression for $P(B)$ into the denominator of the earlier equation gives:[^3]: 8–9 $$P(A_i|B) = \frac{P(A_i) P(B|A_i)}{\sum_j P(A_j) P(B|A_j)} .$$

The events $A$ and not-$A$, written $\neg A$, are a mutually exclusive and exhaustive pair. Therefore the previous formula can be applied, and the sum in the denominator will have only two terms:

$$
P(A| B) = \frac{P(B| A) P(A)}{ P(B| A) P(A) + P(B| \neg A) P(\neg A)}.
$$

[^1]: Stuart, A.; Ord, K. (1994), *Kendall's Advanced Theory of Statistics: Volume I – Distribution Theory*, [Edward Arnold](https://en.wikipedia.org/wiki/Edward_Arnold_(publisher)), §8.7

[^2]: Downey, Allen B. (2012). [*Think Bayes: Bayesian Statistics Made Simple*](https://open.umn.edu/opentextbooks/textbooks/288). Green Tea Press. pp. 3–4. [ISBN](https://en.wikipedia.org/wiki/ISBN_(identifier)) [9781449370787](https://en.wikipedia.org/wiki/Special:BookSources/9781449370787) – via Open Textbook Library, University of Minnesota.

[^3]: Lee, Peter M. (2012). "Chapter 1". [*Bayesian Statistics*](https://www.york.ac.uk/depts/maths/histstat/pml1/bayes/book.htm). [Wiley](https://en.wikipedia.org/wiki/John_Wiley_&_Sons). [ISBN](https://en.wikipedia.org/wiki/ISBN_(identifier)) [978-1-1183-3257-3](https://en.wikipedia.org/wiki/Special:BookSources/978-1-1183-3257-3).
