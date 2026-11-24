# Acquisition policy selection

Across nine development opponents, the new bidding policy increased the mean outright win rate by **2.03 percentage points**. The paired 95% interval was **+1.36 to +2.70 points**. Excluding the previously known qualifier, the improvement was **0.86 points** with an interval of **+0.18 to +1.54 points**.

Each row uses 2,400 fresh games per policy. Both policies received identical game and rival-variation seeds in both starting seats. Games still running after 1,000 turns counted as nonwins.

| Opponent | Prior policy | New policy | Change in percentage points |
|---|---:|---:|---:|
| Previously known qualification challenger | 46.25% | 57.67% | +11.42 |
| Random neural bidders | 59.29% | 61.79% | +2.50 |
| Aggressive neural bidders | 60.21% | 64.33% | +4.12 |
| Known blocker | 77.13% | 77.13% | 0.00 |
| Rollout legacy | 61.92% | 61.21% | −0.71 |
| Additive pricing | 44.46% | 42.29% | −2.17 |
| Frozen neural | 55.08% | 55.58% | +0.50 |
| Cautious | 90.54% | 89.58% | −0.96 |
| Previously unseen hybrid pricing | 73.63% | 77.21% | +3.58 |

The previous qualification challenger became a known training opponent for this experiment. The additive-pricing family exposed a weakness in the first confirmation, where the two policies won 43.8% and 45.3%. It then entered second-round selection. On fresh seeds the gated finalist regressed against this family by 2.17 points, with a paired interval of −4.22 to −0.12. The hybrid family used a different pricing formula and first appeared in the last development confirmation.

The first round screened 13 sets of acquisition weights, advanced three finalists, and checked one on fresh seeds. It improved results against neural bidders but lost 3.2 points against the known blocker. The second round kept the prior policy until observed trades or prices ruled out that blocker. Every blocker outcome matched on the final development seeds. Selection used 73,200 games across both rounds, with 81,600 additional games for confirmations.

The reproducible training command is `python3 resilience_train.py`. Its report records each candidate, the seed-block outcomes, model and source hashes, and intervals calculated from paired seed-block differences. A challenger newly fitted against the selected deployment is a separate qualification test.
