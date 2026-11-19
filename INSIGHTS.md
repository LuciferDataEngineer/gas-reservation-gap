# Method notes

Snapshot: 20 November 2026, 9:14 am AEDT. Gas days run to 18 November 2026. Victorian and short-term prices run to 31 October 2026. Cargo records run to 30 September 2026.

## Question

Would reserving up to 20% of east-coast (Gladstone / Curtis Island) LNG exports close the southern states' winter gas shortfall, and how much would be left over?

Southern states here are Victoria, New South Wales, South Australia and Tasmania. Western Australia is outside this system and is the jurisdiction the bill discussion has treated as already covered by its own reservation.

Winter is 1 June to 31 August. That is when AEMO's published peak days fall.

## Layers

1. `dlt` loads the committed parquet snapshot into a DuckDB schema named bronze. The pipeline does not call the network.
2. Ibis, pointed at that DuckDB file, sums Curtis Island feed gas. The same sum in DuckDB SQL must match to 0.01 TJ or the build stops.
3. DuckDB builds the daily joined table and the peak-day and annual comparisons.
4. Ten SQL checks cover row counts, the three export plants, non-negative feed gas, duplicate facility-days, Iona inventory range, the 2024 feed-gas cross-check against the outlook's LNG line, actual and outlook coverage, heating degrees, positive morning prices, and the 512 TJ/day westbound pipeline figure.
5. When Nessie and the object store are up, the daily feed-gas total is written to an Iceberg table on a Nessie branch named `audit` and read back. The sum has to match. `main` is then moved to that hash and read again. Nessie's merge endpoint returns no common ancestor for these catalog commits, so the publish is a fast-forward of `main`. A failed check leaves `main` where it was.
6. When Feldera is up, the same facility-days are aggregated with incremental SQL and the total must match DuckDB.

## Definitions

| Term | Rule |
| --- | --- |
| Feed gas | Sum of bulletin-board `Demand` for facilities typed `LNGEXPORT` at Curtis Island. Pipelines that deliver to the plants repeat the same quantity and are not added. |
| Exports | Sum of shipment `VolumePJ`. This is cargo energy, not feed gas. |
| 20% reserve | 0.20 times the volume named in the sentence. Annual comparisons for 2026 onward use the outlook LNG line, because a future cargo file does not exist. 2025 also reports 20% of actual cargoes. |
| Peak-day gap | Southern demand minus a supply stack, on the day demand is highest that year. Positive means demand is above that stack. |
| Full stack | AEMO's published line "maximum southern production, SWQP, deep and shallow storage, and anticipated supply". |
| Local production | The published "maximum southern production" line. |
| North-south pipe | The difference between "production plus expected SWQP" and production. Nameplate westbound capacity is a separate published cell: 512 TJ/day. |
| Annual gap | Step Change scenario, southern domestic supply gap, from the outlook's figure workbook. |
| Demand-calibrated obligation | `min(1.10 × east-coast domestic consumption excluding gas power, 0.20 × that year's outlook LNG)`. Domestic consumption is the Step Change line. |
| Iona draw | Reported inventory on 31 May minus inventory on 31 August. |
| Heating degree | `max(0, 18 − daily mean °C)` at the city coordinate requested from Open-Meteo. |
| Morning price | Victorian wholesale price at 6:00, the interval AEMO names as the start-of-day reference. |

Feed gas in 2024 is 1,439.0 PJ. The outlook's LNG line for 2024 is 1,439.0 PJ. The build requires those to be within 1 PJ.

Cargo energy is lower than feed gas (2025 cargoes 1,336.0 PJ, feed gas 1,423.1 PJ). The pages call cargoes "exports" and feed gas "gas the plants took". The difference is not assigned to a fuel factor, because the files do not label it.

## Causal estimate

Question in the data: on days with a price, a feed-gas total, a Melbourne heating degree, Victorian total demand and Longford supply, how does the 6:00 price move with feed gas once those other readings are held in a linear adjustment.

Sample: 1327 days, 2023-03-15 to 2026-10-31.

DoWhy backdoor linear regression, treatment `lng_tj`, outcome `price_6am`, common causes heating degree, Victorian demand, Longford supply, and a winter indicator. Estimate: 0.000273 $/GJ per TJ (0.0273 $/GJ per 100 TJ).

Refutation, five simulations, seed 17:

| Check | New effect ($/GJ per TJ) |
| --- | ---: |
| Placebo (permuted treatment) | -0.000053 |
| Random common cause | 0.000273 |
| 80% subset | 0.000214 |

EconML `LinearDML` with linear outcome and treatment models, the same controls, three folds, `random_state=17`. Constant marginal effect 0.000269 $/GJ per TJ. 95% interval [0.000068, 0.000470]. The interval excludes zero.

Correlation of the Melbourne heating degree and the morning price, same rows, no controls: 0.252.

This is not an estimate of the bill. Feed gas is chosen by the plants. The controls do not include contract positions, outages, or pipeline constraints. A placebo that stays near the original effect would weaken the claim; the subset check should stay near it.

Plain-language line used on the prices page: Once the cold, Victorian demand and Longford supply are taken into account, days when the export plants took more gas did not come with a clearly higher or lower Melbourne morning price.

## What the arithmetic does not do

- It does not move gas along the pipeline. If the outlook already counts the north-south pipe and Iona at their withdrawal and flow limits, extra gas at Gladstone does not appear in Melbourne on the same day.
- It does not re-solve AEMO's network model. The stacks are the published lines.
- 2026 to 2030 daily demand in the workbook is the outlook on a reference weather year, not a record of those future dates. 2024 and 2025 in that workbook are marked actual.
- On 18 June 2025, bulletin-board pipeline deliveries into Victoria, New South Wales, South Australia and Tasmania summed to 2,105.1 TJ. AEMO's own southern demand that day was 2,187.8 TJ. The pages use AEMO's series. Pipeline deliveries are not added to power-station or large-user rows, because those rows repeat gas already delivered.
- Longford's facility row lists 720 TJ/day in the capacity column and about 1,175 TJ/day in the description. Measured averages are reported separately and are not forced to either figure.
- No Asian netback is published in these free files. Quarterly transaction prices are shown only for quarters AEMO filled in, and they are not labelled as a netback here.
- Not a trading, contracting or investment recommendation.
