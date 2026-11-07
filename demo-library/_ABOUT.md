# The sample Library

These four files are the AI Lodge demo's sample course materials. They describe a
**fictional** course (CC101 Cloud Computing Foundations): every name, email,
date and rule is made up. They were written for the demo and are released under
[CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).

The student's own evaluation (21 questions, README "Solution overview") used real
university course files, which are not in this repository and are not shown in the demo.

Some questions worth trying (each tests something Anchor is built for):

| Question | What it shows |
| --- | --- |
| What happens if I submit a lab 2 days late? | a rule quoted from the handbook |
| When does Quiz 1 close? | a date worked out from the quiz rule (the number check) |
| Which port must the lab web server use? | a quote with a line range from a text file |
| When is the group project demo? | two files disagree (handbook vs announcement): both are reported |
| Who sets the final exam paper? | not in the materials: Anchor says so |
| How do containers keep their memory use apart? | an explanation from lecture notes |

`scripts/load_demo_library.py` loads every `.md`, `.txt` and `.pdf` here whose name
doesn't start with `_`.
