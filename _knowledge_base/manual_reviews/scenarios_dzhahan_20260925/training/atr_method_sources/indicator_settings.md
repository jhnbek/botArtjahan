# MM\-040\-024

[Каталог](../../README.md)

Материал импортирован. Статус `imported_unverified` не подтверждает содержательную достоверность.

Статус: imported\_unverified

## Формулировка

ATR для текущего дня считается от закрытия прошлого дня до текущей цены\. Скрипт ATR в Pine Editor использует последние 5 дней, исключает паранормально большие бары больше 1,5 ATR и маленькие меньше 50% ATR, а 10% ATR служит ориентиром нормального стопа; важно смотреть дневной ATR на дневном таймфрейме, а не путать его с M5\-ATR\.

## Цитата

точка ATR, нулевая точка, считается от закрытия прошлого дня и до текущей цены

## Интерпретация

Инструментальный слой должен явно хранить timeframe\_of\_atr и не смешивать дневной ATR с текущим младшим таймфреймом\.

## Сохранённые свидетельства

- [legacy\_source\_frame\_reference: 02:14:07.620](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/frame-0006.md#e-5df6176edb5d1dfb884a454c3bd6da99fab7fc488846a3ee8b6aeea5ccaf4c35)
  [Открыть кадр](../../../blobs/4a/4af87e8122754d20b4224cd0e26380102937e8bad94f1e2f04e0a723ef43ad36)
- [legacy\_source\_frame\_reference: 02:16:12.460](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/frame-0006.md#e-0d9da7969a2b2f47cd66b66217b626b42400775b992f125dfe50dc6232be238f)
  [Открыть кадр](../../../blobs/51/513967d0887948aedb7b7d40bd2b70fab679b6e255fdda5b00ecbb9defe015da)
- [legacy\_source: 02:12:11.910](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-cc34e2b2389e501a930f230b813cf91c8e80dfaa1ea491e5c22d8aa00cfe5120)
  [Открыть кадр](../../../blobs/13/13418f0059b646a46118f6c73b311a974ed7ab063492c65b54292838d749bf25)
- [legacy\_source: 02:11:49.720](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-33334a6233c7a8e5af137b2b1d2628af325ae487e0a7dc1212476aeb6c4212a4)
  [Открыть кадр](../../../blobs/35/35c0e8779a329b0c36cc9f52135faa270ffb04fd4da6eafc510c42041fd469f0)
  [Открыть кадр](../../../blobs/75/75ca1517599800faa0c82f9d5ff07d24912d47d35650d1f1a69e1b7da6e7e0d9)
- [legacy\_source: 02:14:30.830](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-aa4981d596008c00415de55620fcaf337f6552a0e3ca12e84f94dd74d5913832)
- [legacy\_source\_frame\_reference: 02:13:13.860](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/frame-0006.md#e-2c50a85892e0771457946c1a4234bcaba5fbb220756ff9ca7bf59b585532b3ff)
  [Открыть кадр](../../../blobs/3f/3ffe96f885dc20b8907a1a308692853c2f71f91c24e0f2779d7ba47e9f0f2f9f)
- [legacy\_source: 02:16:06.450](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-7d6220a62d001da9b259098f5f2c5938c4b42405eabb0f5dbd46670c983fa962)
  [Открыть кадр](../../../blobs/51/513967d0887948aedb7b7d40bd2b70fab679b6e255fdda5b00ecbb9defe015da)
- [legacy\_source\_frame\_reference: 02:11:50.240](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/frame-0006.md#e-76b6ec6a0348ab77ed3e96bf810a1a4677bf264716ae275e2912b0b1cc298d79)
  [Открыть кадр](../../../blobs/35/35c0e8779a329b0c36cc9f52135faa270ffb04fd4da6eafc510c42041fd469f0)
- [legacy\_source: 02:13:46.340](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-e8f2a24a54798420ad5e8db061d5e6af51a1bf7df6fc2535455f31aca5c5c7a5)
- [legacy\_source: 02:10:53.610](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-0c96c02553418628301eedc14b8d1d284ca0d5b8575309346728f93dbd811bb8)
- [legacy\_source\_frame\_reference: 02:11:47.300](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/frame-0006.md#e-3efdee67fb6b0f8641dc805325aa23e7786b2b189e48bc7954c731944b5069a3)
  [Открыть кадр](../../../blobs/3b/3b56eda7abe4319b90e88fb57040bc0c1c0abd2d67d04e56e4851fcde9625efa)
- [legacy\_source\_frame\_reference: 02:15:16.080](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/frame-0006.md#e-c2765fc9de61979d4c55bb0b1357e3d71d995d3cdaeba00df1b5fb868f21c188)
  [Открыть кадр](../../../blobs/35/35d6221e0a2d3cac984df9e4c92ee0c96fa33a42e46a1794abadd914bd98610d)
- [legacy\_source: 02:11:24.620](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-905d8d2e9ceac4a55348ff5a5f8b5cc4175ea91545c04aa0cd148743047c02d4)
  [Открыть кадр](../../../blobs/3b/3b56eda7abe4319b90e88fb57040bc0c1c0abd2d67d04e56e4851fcde9625efa)
- [legacy\_source\_frame\_reference: 02:15:38.520](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/frame-0006.md#e-7ad3e9f8c08681f870fd522d0f044d2c1041301e22a21f8c786dece8baee3156)
  [Открыть кадр](../../../blobs/56/5660f02badca31d555d61a2f1117d3a7b97482d7b2fab45387166b9de3eac68b)
- [legacy\_source: 02:12:40.300](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-0c60c2777760e90809565b3c87bea4f7bdd6ad31d12b6a555d4e938f96c5895e)
  [Открыть кадр](../../../blobs/3f/3ffe96f885dc20b8907a1a308692853c2f71f91c24e0f2779d7ba47e9f0f2f9f)
- [legacy\_source: 02:15:33.500](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-17ad252c8bf69ee1bec5dd8d5298c9ff0fd50f18a870271495b06637375f4309)
  [Открыть кадр](../../../blobs/56/5660f02badca31d555d61a2f1117d3a7b97482d7b2fab45387166b9de3eac68b)
  [Открыть кадр](../../../blobs/5a/5aedd7d280bcb95b20abbfe40d7059fe6bbc0895c38b3fa55d150c1ba8ab9891)
- [legacy\_source: 02:14:05.560](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-d6fa5ffe271ba168c7a6c6dce691c21546b7642142e82d27f745af76957e882c)
  [Открыть кадр](../../../blobs/4a/4af87e8122754d20b4224cd0e26380102937e8bad94f1e2f04e0a723ef43ad36)
- [legacy\_source: 02:15:03.950](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-03f50550707bf91e39508f7af9279d764885dbbe3acfd373d4f92081733cfc4a)
  [Открыть кадр](../../../blobs/35/35d6221e0a2d3cac984df9e4c92ee0c96fa33a42e46a1794abadd914bd98610d)
- [legacy\_source\_frame\_reference: 02:15:42.000](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/frame-0006.md#e-3735f149a7338d80fce5d74e057edc23ed388856ac58275876604676dbc9bd45)
  [Открыть кадр](../../../blobs/5a/5aedd7d280bcb95b20abbfe40d7059fe6bbc0895c38b3fa55d150c1ba8ab9891)
- [legacy\_source\_frame\_reference: 02:12:33.900](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/frame-0006.md#e-bf2832b6e197b5d7ee5ba17338c5e54cdab2e0da3b61a53b6ff8c58e996aac81)
  [Открыть кадр](../../../blobs/13/13418f0059b646a46118f6c73b311a974ed7ab063492c65b54292838d749bf25)
- [legacy\_source\_frame\_reference: 02:11:55.620](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/frame-0006.md#e-04927cbabbcc5fbcd82b2b8530aca96e71ebc16e06459510f743fc79b49f54c2)
  [Открыть кадр](../../../blobs/75/75ca1517599800faa0c82f9d5ff07d24912d47d35650d1f1a69e1b7da6e7e0d9)
- [legacy\_source: 02:13:22.670](../../lectures/9377d3ab8d3e5f2af6180dedba667dfacc6f6d7cc8077915c95c579c69ebe277/source_unit-0003.md#e-21ce6fb746f3735c9b2e937ee45e758c33cb4a48072416a9d18f166e54357645)
