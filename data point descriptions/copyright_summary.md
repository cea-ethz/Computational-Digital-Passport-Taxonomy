# Copyright summary for source PDFs

Each `<N>_response.json` produced by the LLMs cites up to two source PDFs in its `references` array and embeds the verbatim passage that the retrieval step returned. Only a subset of those PDFs are licensed in a way that lets us redistribute the verbatim passage; the rest must have their `text` field redacted before the JSONs are shared.

The authoritative source is [copyright details.txt](../data_point_descriptions/copyright%20details.txt) (read by `filter_copyright.py` at runtime). This document is a human-readable snapshot of the same information, sorted alphabetically by PDF filename. **If the two ever disagree, the `.txt` file wins.**

## Filter rule

The script keeps the verbatim `text` only when the PDF filename is mapped (in `apa_mapping`) to an APA citation whose `dataset_config` entry has `can_extract: True`. Every other case — restricted PDF, or filename not in `apa_mapping` at all — gets:

> `"exact text reference is restricted by copyright of the original publication"`

## PDF licensing table

| PDF file | APA citation | License | Can extract | Notes |
|---|---|---|---|---|
| Atta 2021.pdf | Atta et al., 2021 | All Rights Reserved | No | Exclude completely. © 2021 Elsevier Ltd. All rights reserved. |
| BAMB 2019.pdf | Heinrich & Lang, 2019 | All Rights Reserved | No | Exclude exact text references. © 2019 Matthias Heinrich, Werner Lang. All rights reserved. |
| Bauen digital Schweiz 2024.pdf | Bauen digital Schweiz, 2024 | CC BY-NC-SA 4.0 | Yes | Safe under CC BY-NC-SA 4.0 International. |
| Bosma 2024.pdf | Bosma, 2024 | All Rights Reserved | No | Exclude exact text references. © 2024 Bosma. All rights reserved. |
| Byers 2025.pdf | Byers et al., 2025 | CC BY 4.0 | Yes | Safe under CC BY 4.0. |
| Christensen 2025.pdf | Christensen et al., 2025 | CC BY-NC-ND 4.0 | Yes | Safe under CC BY-NC-ND 4.0. |
| Circularise 2025.pdf | Circularise, 2025 | All Rights Reserved | No | Exclude exact text references. © 2025 Circularise. All rights reserved (by default). |
| CPR 2024.pdf | Construction Products Regulation, 2024 | EU Public Domain | Yes | Safe under EUR-Lex reuse policy and Commission Decision 2011/833/EU. |
| ESPR 2024.pdf | Ecodesign for Sustainable Products Regulation, 2024 | EU Public Domain | Yes | Safe under EUR-Lex reuse policy and Commission Decision 2011/833/EU. |
| Giovanardi 2023.pdf | Giovanardi et al., 2023 | CC BY 4.0 | Yes | Safe under CC BY 4.0. |
| Göswein 2022.pdf | Göswein et al., 2022 | CC BY 3.0 | Yes | Safe under CC BY 3.0. |
| Heisel and Rau-Oberhuber 2020.pdf | Heisel & Rau-Oberhuber, 2020 | All Rights Reserved | No | Exclude completely. © 2021 Elsevier Ltd. All rights reserved. |
| Honic 2019.pdf | Honic et al., 2019 | All Rights Reserved | No | Exclude completely. © 2021 Elsevier Ltd. All rights reserved. |
| Honic 2021.pdf | Honic et al., 2021 | CC BY 4.0 | Yes | Safe under CC BY 4.0. |
| ISO 59040 2025.pdf | ISO 59040, 2025 | Proprietary | No | Exclude completely. ISO standards are sold under proprietary licence. |
| Jensen 2023.pdf | Jensen et al., 2023 | CC BY 4.0 | Yes | Safe under CC BY 4.0. |
| KC 2024.pdf | KC et al., 2024 | Restricted | No | Exclude completely. © Emerald Publishing Limited 2024. Licensed re-use rights only. |
| Kebede 2024.pdf | Kebede et al., 2024 | CC BY 4.0 | Yes | Safe under CC BY 4.0. |
| Lopes and Barata 2024.pdf | Lopes & Barata, 2024 | CC BY-NC-ND 4.0 | Yes | Safe under CC BY-NC-ND 4.0. |
| Mao and Cao 2025.pdf | Mao & Cao, 2025 | All Rights Reserved | No | Exclude completely. © 2025 Elsevier Ltd. All rights reserved. |
| Markou 2025.pdf | Markou et al., 2025 | CC BY-NC 4.0 | Yes | Safe under CC BY-NC 4.0. |
| Mulhall 2022.pdf | Mulhall et al., 2022 | CC BY 4.0 | Yes | Safe under CC BY 4.0. |
| Munaro and Tavares 2021.pdf | Munaro & Tavares, 2021 | Restricted | No | Exclude completely. © Emerald Publishing Limited. Licensed re-use rights only. |
| Platform CB 2023.pdf | Platform CB'23, 2022 | Permissive/Custom | Yes | Safe — reuse explicitly granted in the publication. |
| Ruismäki 2025.pdf | Ruismäki et al., 2025 | CC BY 4.0 | Yes | Safe under CC BY 4.0. |
| Seddiqui 2024.pdf | Seddiqui et al., 2024 | CC BY 4.0 | Yes | Safe under CC BY 4.0. |
| Stratmann 2023.pdf | Stratmann et al., 2023 | CC BY 3.0 DE | Yes | Safe under CC BY 3.0 DE (Creative Commons Attribution 3.0 Germany). |
| Van Capelleveen 2023.pdf | Van Capelleveen et al., 2023 | CC BY-NC-ND 4.0 | Yes | Safe under CC BY-NC-ND 4.0. |
| Wan and Jiang 2025.pdf | Wan & Jiang, 2025 | CC BY 4.0 | Yes | Safe under CC BY 4.0. |
| Çetin 2023.pdf | Çetin et al., 2023 | CC BY-NC-ND 4.0 | Yes | Safe under CC BY-NC-ND 4.0. |

## Tallies

- Safe to extract (verbatim text kept): **20 PDFs**
- Restricted (verbatim text redacted): **10 PDFs**
