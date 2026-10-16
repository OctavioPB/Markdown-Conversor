# DRAFT — requiere revisión humana (borrador de Pandoc adaptado al dialecto)

# Teorema de Bayes

*Un [letrero de neón](https://es.wikipedia.org/wiki/Letrero_de_ne%C3%B3n) que muestra el enunciado del teorema de Bayes*

El **teorema de Bayes**, en la [teoría de la probabilidad](https://es.wikipedia.org/wiki/Probabilidad), es una proposición planteada por el matemático inglés [Thomas Bayes](https://es.wikipedia.org/wiki/Thomas_Bayes) (1702-1761)[^1] y publicada póstumamente en 1763,[^2] que expresa la [probabilidad condicional](https://es.wikipedia.org/wiki/Probabilidad_condicionada) de un [evento aleatorio](https://es.wikipedia.org/wiki/Suceso_aleatorio) $A$ dado *$B$* en términos de la distribución de probabilidad condicional del evento $B$ dado $A$ y la [distribución de probabilidad marginal](https://es.wikipedia.org/wiki/Distribuci%C3%B3n_marginal) de solo $A$.

En términos más generales y menos matemáticos, el teorema de Bayes es de enorme relevancia puesto que vincula la probabilidad de $A$ dado $B$ con la probabilidad de $B$ dado $A$. Es decir, por ejemplo, que sabiendo la probabilidad de tener un dolor de cabeza dado que se tiene gripe, se podría saber (si se tiene algún dato más), la probabilidad de tener gripe si se tiene un dolor de cabeza. Muestra este sencillo ejemplo la alta relevancia del teorema en cuestión para la ciencia en todas sus ramas, puesto que tiene vinculación íntima con la comprensión de la probabilidad de aspectos causales dados los efectos observados.

Una de las muchas aplicaciones del teorema de Bayes es la [inferencia bayesiana](https://es.wikipedia.org/wiki/Inferencia_bayesiana), un enfoque particular de la [inferencia estadística](https://es.wikipedia.org/wiki/Inferencia_estad%C3%ADstica). Cuando se aplican, las probabilidades implicadas en el teorema pueden tener diferentes [interpretaciones de probabilidad](https://es.wikipedia.org/wiki/Interpretaciones_de_las_probabilidades). Con la interpretación [probabilidad bayesiana](https://es.wikipedia.org/wiki/Probabilidad_bayesiana), el teorema expresa cómo un grado de creencia, expresado como una probabilidad, debería cambiar racionalmente para tener en cuenta la disponibilidad de pruebas relacionadas. La inferencia bayesiana es fundamental para la [estadística bayesiana](https://es.wikipedia.org/wiki/Estad%C3%ADstica_bayesiana), siendo considerada por una autoridad como; "a la teoría de la probabilidad lo que el [teorema de Pitágoras](https://es.wikipedia.org/wiki/Teorema_de_Pit%C3%A1goras) es a la geometría."[^3]

## Teorema

Sea $\{A_1, A_2, ..., A_i, ..., A_n\}$ un conjunto de sucesos mutuamente excluyentes y exhaustivos tales que la probabilidad de cada uno de ellos es distinta de cero $(\operatorname{P}[A_i]\neq0$ para $i=1,2,\dots,n)$. Si $B$ es un suceso cualquiera del que se conocen las probabilidades condicionales $P(B|A_i)$ entonces la probabilidad $P(A_i|B)$ viene dada por la expresión:

$$
P(A_i|B) = \frac{P(B|A_i) P(A_i)}{P(B)}
$$

donde:

- $P(A_i)$ son las probabilidades *a priori*,
- $P(B|A_i)$ es la probabilidad de $B$ en la hipótesis $A_i$,
- $P(A_i|B)$ son las probabilidades *a posteriori*.

## Fórmula de Bayes

*La visualización del teorema de Bayes por la superposición de dos [árboles de decisión](https://es.wikipedia.org/wiki/%C3%81rbol_de_decisi%C3%B3n)*

El teorema de Bayes se deduce de la definición de [probabilidad condicionada](https://es.wikipedia.org/wiki/Probabilidad_condicionada), la Fórmula de Bayes, también conocida como Regla de Bayes:

$$
P(A_i|B) = \frac{P(B|A_i) P(A_i)}{ P(B)}\cdots [1]
$$

nos permite calcular la probabilidad condicional $P(A_i|B)$ de cualquiera de los eventos $A_i$ dado $B$. La fórmula $[1]$ «ha originado muchas especulaciones filosóficas y controversias».[^4]

[^1]: Diccionarios Oxford-Complutense. Matemáticas de Christopher Clapham 84-89784-566

[^2]: Bayes, Thomas (1763). «An Essay towards solving a Problem in the Doctrine of Chances.». *[Philosophical Transactions of the Royal Society of London](https://es.wikipedia.org/wiki/Philosophical_Transactions_of_the_Royal_Society_of_London)* **53**: 370-418. [doi](https://es.wikipedia.org/wiki/Digital_object_identifier):[10.1098/rstl.1763.0053](https://dx.doi.org/10.1098%2Frstl.1763.0053).

[^3]: Jeffreys, Sir Harold (1973). [*Inferencia científica.*](https://www.worldcat.org/oclc/764571529) (en inglés). Cambridge: At the University Press. [OCLC](https://es.wikipedia.org/wiki/OCLC) [764571529](https://www.worldcat.org/oclc/764571529).

[^4]: Parzen, Emanuel. *Teoría moderna de probabilidades y sus aplicaciones*. Limusa Grupo Noriega Editores. [ISBN](https://es.wikipedia.org/wiki/ISBN) [978-9681-807351](https://es.wikipedia.org/wiki/Especial:FuentesDeLibros/978-9681-807351).
