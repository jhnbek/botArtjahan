# Full working-level selection audit

As of 2026-09-23T06:00:00Z; closed bars: 701; retained within 18 months: 549.

| Metric | Count |
|---|---:|
| raw_candidates | 159 |
| raw_mirror_limit | 90 |
| general_selected_all_types | 27 |
| general_selected_mirror_limit | 24 |
| with_manual_rejections_all_types | 20 |
| with_manual_rejections_mirror_limit | 17 |
| explicit_58_still_found_by_general_rules | 5 |
| explicit_58_still_found_with_manual_constraints | 0 |
| reviewed_whole_level_constraints | 29 |
| reviewed_whole_levels_still_found_by_general_rules | 6 |
| reviewed_whole_levels_still_found_with_manual_constraints | 0 |
| reference_price_matches | 13 |
| reference_count | 19 |
| selected_without_reference_match | 4 |

## All selected levels

| Price | Strength | Mode |
|---:|---:|---|
| 58042.6 | 8.402 | inflection, limit_level |
| 59081.4 | 6.622 | limit_level, strong_movement_stop |
| 62235.4 | 9.731 | limit_level, mirror_level |
| 67287.8 | 7.597 | limit_level, mirror_level |
| 71805.2 | 7.371 | limit_level, mirror_level |
| 74900.0 | 19.027 | inflection, limit_level, mirror_level, round_number |
| 79388.5 | 11.115 | limit_level, mirror_level |
| 81787.0 | 9.491 | inflection, limit_level |
| 83560.0 | 7.718 | limit_level, mirror_level |
| 90025.0 | 8.116 | limit_level, mirror_level |
| 94000.0 | 10.219 | limit_level, mirror_level, paranormal_bar, round_number |
| 97963.2 | 19.119 | inflection, limit_level, mirror_level |
| 101045.9 | 17.773 | inflection, paranormal_bar |
| 108239.6 | 12.272 | limit_level, mirror_level |
| 110485.7 | 15.567 | limit_level, mirror_level |
| 112577.7 | 9.508 | limit_level, mirror_level |
| 117896.7 | 12.959 | limit_level, mirror_level |
| 120321.0 | 5.527 | limit_level, strong_movement_stop |
| 123742.2 | 7.623 | inflection |
| 125849.7 | 8.795 | inflection |

## Every candidate decision

| Price | Decision | Reasons | Stronger neighbour |
|---:|---|---|---:|
| 58042.6 | kept |  |  |
| 59081.4 | kept |  |  |
| 60614.5 | rejected | no_confirmed_structural_basis |  |
| 60711.1 | rejected | explicit_user_rejection |  |
| 61476.4 | rejected | explicit_user_rejection, no_fast_strong_reaction |  |
| 61777.6 | rejected | no_confirmed_structural_basis, explicit_user_rejection, insufficient_clean_confirmations |  |
| 62216.4 | rejected | no_confirmed_structural_basis, weak_price_origin |  |
| 62235.4 | kept |  |  |
| 62505.1 | rejected | no_confirmed_structural_basis, explicit_user_rejection |  |
| 62846.8 | rejected | explicit_user_rejection |  |
| 62972.3 | rejected | no_confirmed_structural_basis |  |
| 63209.3 | rejected | explicit_user_rejection |  |
| 63869.5 | rejected | explicit_user_rejection, no_fast_strong_reaction |  |
| 64250.0 | rejected | explicit_user_rejection, no_fast_strong_reaction |  |
| 64581.8 | rejected | explicit_user_rejection |  |
| 64703.2 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction |  |
| 64979.7 | rejected | explicit_user_rejection, weak_price_origin |  |
| 65065.0 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 65573.5 | rejected | no_confirmed_structural_basis, explicit_user_rejection |  |
| 65666.0 | rejected | no_confirmed_structural_basis, explicit_user_rejection |  |
| 65747.8 | rejected | weak_price_origin |  |
| 66451.1 | rejected | explicit_user_rejection, weak_price_origin |  |
| 66937.7 | rejected | no_confirmed_structural_basis, explicit_user_rejection, insufficient_clean_confirmations |  |
| 67287.8 | kept |  |  |
| 67300.1 | rejected | no_confirmed_structural_basis, weak_price_origin |  |
| 67679.0 | rejected | explicit_user_rejection |  |
| 68224.4 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 68687.1 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction |  |
| 69150.0 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 69301.0 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction |  |
| 69989.8 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin, insufficient_clean_confirmations |  |
| 70508.8 | rejected | explicit_user_rejection, weak_price_origin |  |
| 70937.0 | rejected | no_confirmed_structural_basis, explicit_user_rejection, no_fast_strong_reaction, weak_price_origin, insufficient_clean_confirmations |  |
| 71343.9 | rejected | explicit_user_rejection, no_fast_strong_reaction |  |
| 71805.2 | kept |  |  |
| 71984.8 | rejected | no_confirmed_structural_basis, insufficient_clean_confirmations |  |
| 72460.7 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 73323.9 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 73653.6 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction |  |
| 73800.0 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 74021.8 | rejected | no_confirmed_structural_basis, explicit_user_rejection, insufficient_clean_confirmations |  |
| 74221.4 | rejected | explicit_user_rejection, no_fast_strong_reaction |  |
| 74456.2 | rejected | no_confirmed_structural_basis, explicit_user_rejection |  |
| 74900.0 | kept |  |  |
| 75550.0 | rejected | explicit_user_rejection |  |
| 76053.9 | rejected | no_confirmed_structural_basis |  |
| 76199.1 | rejected | no_confirmed_structural_basis, explicit_user_rejection |  |
| 76200.9 | rejected | explicit_user_rejection |  |
| 76644.3 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 76824.3 | rejected | no_confirmed_structural_basis, explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 77371.9 | rejected | explicit_user_rejection, no_fast_strong_reaction, unresolved_chop |  |
| 77847.6 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 78170.4 | rejected | explicit_user_rejection, weak_price_origin |  |
| 78173.0 | rejected | no_confirmed_structural_basis |  |
| 78332.6 | rejected | no_confirmed_structural_basis, explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 78610.0 | rejected | explicit_user_rejection, no_fast_strong_reaction |  |
| 79120.0 | rejected | no_confirmed_structural_basis, explicit_user_rejection, no_fast_strong_reaction |  |
| 79388.5 | kept |  |  |
| 79752.5 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 79909.7 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin, insufficient_clean_confirmations |  |
| 80206.3 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 80607.9 | rejected | explicit_user_rejection, no_fast_strong_reaction |  |
| 80833.6 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 81399.0 | rejected | explicit_user_rejection, no_fast_strong_reaction |  |
| 81787.0 | kept |  |  |
| 82719.2 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 83560.0 | kept |  |  |
| 83755.3 | rejected | no_confirmed_structural_basis, explicit_user_rejection |  |
| 84288.0 | rejected | explicit_user_rejection |  |
| 84408.1 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 85211.1 | rejected | no_fast_strong_reaction |  |
| 86031.4 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction |  |
| 86099.0 | rejected | no_fast_strong_reaction |  |
| 86444.0 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction |  |
| 86753.0 | rejected | explicit_user_rejection |  |
| 87212.0 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction |  |
| 87521.9 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 87680.0 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 88560.1 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 89243.1 | rejected | explicit_user_rejection |  |
| 89262.0 | rejected | no_confirmed_structural_basis |  |
| 90025.0 | kept |  |  |
| 90588.8 | rejected | no_confirmed_structural_basis |  |
| 90945.0 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 91911.8 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 92750.0 | rejected | no_confirmed_structural_basis |  |
| 92950.0 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 93081.0 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 93333.5 | rejected | no_confirmed_structural_basis |  |
| 93538.4 | rejected | explicit_user_rejection, weak_price_origin |  |
| 94000.0 | kept |  |  |
| 94189.4 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 94511.7 | rejected | explicit_user_rejection |  |
| 94550.0 | rejected | no_confirmed_structural_basis |  |
| 95700.1 | rejected | explicit_user_rejection, weak_price_origin |  |
| 95724.5 | rejected | no_confirmed_structural_basis |  |
| 96865.0 | rejected | too_close_to_stronger_level | 97963.2 |
| 97963.2 | kept |  |  |
| 100350.0 | rejected | no_confirmed_structural_basis, insufficient_clean_confirmations |  |
| 100688.0 | rejected | no_confirmed_structural_basis |  |
| 101045.9 | kept |  |  |
| 101046.0 | rejected | too_close_to_stronger_level | 101045.9 |
| 102000.0 | rejected | no_confirmed_structural_basis, insufficient_clean_confirmations |  |
| 102539.7 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 102626.9 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 103036.8 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 103042.2 | rejected | explicit_user_rejection, no_fast_strong_reaction, weak_price_origin |  |
| 103437.4 | rejected | no_confirmed_structural_basis, explicit_user_rejection |  |
| 103669.4 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 104203.9 | rejected | weak_price_origin |  |
| 104936.7 | rejected | explicit_user_rejection |  |
| 105058.6 | rejected | no_confirmed_structural_basis |  |
| 105500.0 | rejected | weak_price_origin |  |
| 106001.8 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 106225.0 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction |  |
| 106538.4 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction |  |
| 106773.7 | rejected | no_fast_strong_reaction |  |
| 107369.7 | rejected | no_confirmed_structural_basis |  |
| 107421.7 | rejected | explicit_user_rejection |  |
| 107481.0 | rejected | no_confirmed_structural_basis |  |
| 108239.6 | kept |  |  |
| 108550.0 | rejected | no_confirmed_structural_basis |  |
| 108955.5 | rejected | no_confirmed_structural_basis |  |
| 109150.0 | rejected | explicit_user_rejection, weak_price_origin |  |
| 109872.8 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 110485.7 | kept |  |  |
| 110660.8 | rejected | no_confirmed_structural_basis |  |
| 110700.0 | rejected | no_confirmed_structural_basis |  |
| 111187.8 | rejected | explicit_user_rejection, weak_price_origin |  |
| 111968.0 | rejected | too_close_to_stronger_level | 110485.7 |
| 112577.7 | kept |  |  |
| 113238.1 | rejected | explicit_user_rejection |  |
| 113369.4 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 113649.9 | rejected | explicit_user_rejection, weak_price_origin |  |
| 114000.0 | rejected | no_confirmed_structural_basis |  |
| 114061.7 | rejected | weak_price_origin |  |
| 114316.3 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 114710.0 | rejected | weak_price_origin |  |
| 115060.1 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 115738.1 | rejected | explicit_user_rejection |  |
| 115750.0 | rejected | no_confirmed_structural_basis |  |
| 116222.0 | rejected | weak_price_origin |  |
| 116373.3 | rejected | no_confirmed_structural_basis, explicit_user_rejection |  |
| 116719.3 | rejected | no_fast_strong_reaction |  |
| 117374.5 | rejected | no_confirmed_structural_basis, no_fast_strong_reaction, weak_price_origin |  |
| 117401.5 | rejected | too_close_to_stronger_level | 117896.7 |
| 117884.0 | rejected | no_confirmed_structural_basis |  |
| 117896.7 | kept |  |  |
| 118440.3 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 118859.2 | rejected | weak_price_origin |  |
| 119474.9 | rejected | too_close_to_stronger_level | 117896.7 |
| 119844.9 | rejected | no_confirmed_structural_basis |  |
| 120321.0 | kept |  |  |
| 121000.0 | rejected | no_fast_strong_reaction |  |
| 122490.0 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 123250.0 | rejected | no_confirmed_structural_basis, insufficient_clean_confirmations |  |
| 123733.9 | rejected | no_fast_strong_reaction, weak_price_origin |  |
| 123742.2 | kept |  |  |
| 125849.7 | kept |  |  |

## Your 58 rejections and the general rule result

Reasons can overlap. Known rejected prices are then excluded before final selection.

| Price | Your reasons | Your comment | General rule |
|---:|---|---|---|
| 59807.5 | Нет сильной реакции от уровня | Уровень в дальнейшем распиливается и нету сильной рекции на уровень | not a candidate on this snapshot |
| 60711.1 | Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и будет легко распиливаться. Нету сильной реакции от уровня | kept:  |
| 61476.4 | Нет сильной реакции от уровня | Это слабый уровень внутри канала. Он будет легко распиливаться | rejected: no_fast_strong_reaction |
| 62846.8 | Нет сильной реакции от уровня | Это слабый уровень внутри канала. Он будет легко распиливаться (и это уже видно) | rejected: too_close_to_stronger_level |
| 63209.3 | Нет сильной реакции от уровня |  | kept:  |
| 63869.5 | Нет сильной реакции от уровня | Это слабый уровень внутри канала. Он будет легко распиливаться | rejected: no_fast_strong_reaction |
| 64250.0 | Нет сильной реакции от уровня |  | rejected: no_fast_strong_reaction |
| 64581.8 | Нет сильной реакции от уровня |  | kept:  |
| 64979.7 | Нет сильной реакции от уровня |  | rejected: weak_price_origin |
| 66451.1 | Нет сильной реакции от уровня | Это слабый уровень внутри канала. Его легко распиливают | rejected: weak_price_origin |
| 67679.0 | Уровень запилен ценой; Нет сильной реакции от уровня | Это слабый уровень внутри канала. Его легко распиливают | rejected: too_close_to_stronger_level |
| 68224.4 | Уровень запилен ценой; Нет сильной реакции от уровня | Это слабый уровень внутри канала. Его легко распиливают | rejected: no_fast_strong_reaction, weak_price_origin |
| 69150.0 | Уровень запилен ценой; Нет сильной реакции от уровня | Это слабый уровень внутри канала. Его легко распиливают | rejected: no_fast_strong_reaction, weak_price_origin |
| 70508.8 | Уровень запилен ценой; Нет сильной реакции от уровня | Это слабый уровень внутри канала. Его легко распиливают | rejected: weak_price_origin |
| 71343.9 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это слабый уровень внутри канала. Его легко распиливают | rejected: no_fast_strong_reaction |
| 72460.7 | Внутри канала или рыночного шума; Нет сильной реакции от уровня |  | rejected: no_fast_strong_reaction, weak_price_origin |
| 73323.9 | Нет сильной реакции от уровня |  | rejected: no_fast_strong_reaction, weak_price_origin |
| 73800.0 | Другая причина | между уровнями 74 900 и 73 768 очень коротко расстояние около 1.5% а это в свою очередь не дает хороших точек входа, т.к. один бар может пересекать сразу два уровня. Поэтому было принято решение убрать мой уровень 73 768 в пользу более сильного уровня 74 900 | rejected: no_fast_strong_reaction, weak_price_origin |
| 75550.0 | Нет сильной реакции от уровня | Сначала уровень показывается себя хорошо, отбивается лимитными барами 31 января и 1 февраля 2026, далее 28 апреля 2026. Но после 31 января и 1 февраля очень плохая реакция от уровня. Поэтому этот уровень принято было убрать | rejected: too_close_to_stronger_level |
| 76100.0 | Нет сильной реакции от уровня |  | not a candidate on this snapshot |
| 76644.3 | Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_fast_strong_reaction, weak_price_origin |
| 77371.9 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_fast_strong_reaction, unresolved_chop |
| 77847.6 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_fast_strong_reaction, weak_price_origin |
| 78170.4 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: weak_price_origin |
| 78610.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_fast_strong_reaction |
| 79752.5 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_fast_strong_reaction, weak_price_origin |
| 80206.3 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_fast_strong_reaction, weak_price_origin |
| 80819.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 81399.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_fast_strong_reaction |
| 82350.0 | Нет сильной реакции от уровня |  | not a candidate on this snapshot |
| 83457.5 | Нет сильной реакции от уровня |  | not a candidate on this snapshot |
| 84254.3 | Нет сильной реакции от уровня | было всего лишь одно касание, а второй бар имеет сильную реакцию в шорт, но это не говорит о том, что там был сильный уровень, это может просто означать совпадение. В дальнешем не видна сильная реакция на уровень | not a candidate on this snapshot |
| 86050.2 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 86753.0 | Внутри канала или рыночного шума; Нет сильной реакции от уровня; Не подтверждён старшим таймфреймом | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | kept:  |
| 87521.9 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_fast_strong_reaction, weak_price_origin |
| 89243.1 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | kept:  |
| 90945.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_fast_strong_reaction, weak_price_origin |
| 92001.6 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 92950.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_fast_strong_reaction, weak_price_origin |
| 93544.9 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 94511.7 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: too_close_to_stronger_level |
| 96654.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 98888.1 | Мало касаний; Внутри канала или рыночного шума |  | not a candidate on this snapshot |
| 103437.4 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_confirmed_structural_basis |
| 104167.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 105442.9 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 107476.5 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 108560.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 108988.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 110119.4 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 110555.3 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 111187.8 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: weak_price_origin |
| 113238.1 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: too_close_to_stronger_level |
| 113900.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 115360.0 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 115980.1 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
| 116373.3 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | rejected: no_confirmed_structural_basis |
| 116761.3 | Уровень запилен ценой; Внутри канала или рыночного шума; Нет сильной реакции от уровня | Это локальный уровень внутри канала. Он слабый и не годится для точки входа на дневке | not a candidate on this snapshot |
