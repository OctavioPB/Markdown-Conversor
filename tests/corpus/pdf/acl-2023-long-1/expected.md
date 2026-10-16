# DRAFT — requiere revisión humana (transcrito a mano de la fuente: source.txt y la página)

# One Cannot Stand for Everyone! Leveraging Multiple User Simulators to train Task-oriented Dialogue Systems

**Yajiao LIU**¹,², **Xin Jiang**³, **Yichun Yin**³, **Yasheng Wang**³, **Fei Mi**³, **Qun Liu**³, **Xiang Wan**², **Benyou Wang**¹,²

¹The Chinese University of Hong Kong, Shenzhen

²Shenzhen Research Institute of Big Data

³Huawei Noah’s Ark Lab

yajiaoliu@link.cuhk.edu.cn

## Abstract

User simulators are agents designed to imitate human users; recent advances have found that Task-oriented Dialogue (ToD) systems optimized toward a user simulator could better satisfy the need of human users. However, this might result in a sub-optimal ToD system if it is tailored to only one *ad hoc* user simulator, since human users can behave differently. In this paper, we propose a framework called MUST[^1] to optimize ToD systems via leveraging **M**ultiple **U**ser **S**imula**T**ors. The main challenges of implementing the MUST are 1) how to adaptively determine which user simulator to interact with the ToD system at each optimization step, since the ToD system might be over-fitted to some specific user simulators, and simultaneously under-fitted to some others; 2) how to avoid catastrophic forgetting of the adaption for a simulator that is not selected for several consecutive optimization steps. To tackle these challenges, we formulate MUST as a Multi-armed bandits (MAB) problem and provide a method called MUSTadaptive that balances *i*) the *boosting adaption* for adaptive interactions between different user simulators and the ToD system and *ii*) the *uniform adaption* to avoid the catastrophic forgetting issue. With both automatic evaluations and human evaluations, our experimental results on MultiWOZ show that the dialogue system trained by MUST achieves a better performance than those trained by a single user simulator. It also has a better generalization ability when testing with unseen user simulators.

## 1 Introduction

Task-oriented dialogue systems aim to help users accomplish their various tasks (e.g., restaurant reservations) through natural language conversations. Training task-oriented dialogue systems in supervised learning approaches often requires a large amount of expert-labeled dialogues, however collecting these dialogues is usually expensive and time-consuming. Moreover, even with a large amount of dialogue data, some dialogue states may not be explored sufficiently for dialogue systems[^2] (Li et al., 2016b). To this end, many researchers try to build user simulators to mimic human users for generating reasonable and natural conversations. By using a user simulator and sampling user goals, we can train the dialogue system from scratch with reinforcement learning (RL) algorithms. Previous works tend to design better user simulator models (Schatzmann et al., 2007; Asri et al., 2016; Gur et al., 2018; Kreyssig et al., 2018; Lin et al., 2021). Especially, Shi et al. (2019) builds various user simulators and analyzes the behavior of each user simulator in the popular restaurant search task from MultiWOZ (Budzianowski et al., 2018).

In real scenarios, dialogue systems need to face various types of users. A single *ad hoc* user simulator can only represent one or a group of users, while other users might be under-represented. Instead of choosing the best-performing one from many dialogue systems trained by different single user simulators, we believe that it is worth trying to train a dialogue system by leveraging all user simulators simultaneously.

In this paper, we propose a framework called MUST to utilize **M**ultiple **U**ser **S**imula**T**ors simultaneously to obtain a better system agent. There exist several simple ways to implement the MUST framework, including a merging strategy, a continual reinforcement learning (CRL) strategy, and a uniform adaption strategy, namely MUSTmerging, MUSTCRL, and MUSTuniform respectively (See §3.2). However, none of them could effectively tackle the challenges: 1) how to efficiently leverage multiple user simulators to train the dialogue

*Proceedings of the 61st Annual Meeting of the Association for Computational Linguistics Volume 1: Long Papers*, pages 1–21 July 9-14, 2023 ©2023 Association for Computational Linguistics

[^1]: The code is available at https://github.com/kiseliu/must.

[^2]: We use the dialogue systems to refer to the task-oriented dialogue systems for simplicity in this paper.
