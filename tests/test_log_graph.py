import unittest

import kittymock  # noqa: F401
from modules.log.graph import build_graph, NODE, VERT, HORIZ, OUT_R, IN_R


def gutter(row):
    return ''.join(g for g, _ in row['cells'])


class GraphTest(unittest.TestCase):
    def test_linear_single_lane(self):
        commits = [{'sha': 'c', 'parents': ['b']},
                   {'sha': 'b', 'parents': ['a']},
                   {'sha': 'a', 'parents': []}]
        g = build_graph(commits)
        self.assertTrue(all(r['col'] == 0 for r in g))
        self.assertTrue(all(gutter(r) == NODE for r in g))    # линейно — один ствол

    def test_branch_and_merge(self):
        # m — merge (родители d и f2); ветка f1→f2 отходит от d. main-цепочка m→d→c.
        commits = [{'sha': 'm', 'parents': ['d', 'f2']},
                   {'sha': 'f2', 'parents': ['f1']},
                   {'sha': 'f1', 'parents': ['d']},
                   {'sha': 'd', 'parents': ['c']},
                   {'sha': 'c', 'parents': []}]
        g = build_graph(commits)
        self.assertEqual([gutter(r) for r in g],
                         [NODE + HORIZ + OUT_R,        # m: ●─╮ ответвление merge
                          VERT + ' ' + NODE,           # f2: │ ● (ствол сквозной)
                          VERT + ' ' + NODE,           # f1
                          NODE + HORIZ + IN_R,         # d: ●─╯ схождение ветки
                          NODE])                       # c: одиночный ствол
        self.assertEqual([r['col'] for r in g], [0, 1, 1, 0, 0])

    def test_main_stays_on_lane_zero(self):
        # main (по HEAD-ref) держится на лейне 0, даже если ветка f новее в списке
        commits = [{'sha': 'f2', 'parents': ['f1']},           # свежая невлитая ветка
                   {'sha': 'f1', 'parents': ['b']},
                   {'sha': 'm2', 'parents': ['m1'], 'refs': [('main', 'head')]},
                   {'sha': 'm1', 'parents': ['b']},
                   {'sha': 'b', 'parents': []}]
        g = build_graph(commits)
        cols = {c['sha']: r['col'] for c, r in zip(commits, g)}
        self.assertEqual(cols['m2'], 0)                        # main — на стволе
        self.assertEqual(cols['m1'], 0)
        self.assertNotEqual(cols['f2'], 0)                     # ветка — вбок

    def test_lane_colors_differ(self):
        commits = [{'sha': 'm', 'parents': ['d', 'f']},
                   {'sha': 'f', 'parents': ['d']},
                   {'sha': 'd', 'parents': []}]
        g = build_graph(commits)
        node_color = g[0]['cells'][0][1]                       # ● ствола
        corner_color = g[0]['cells'][2][1]                     # ╮ ветки
        self.assertNotEqual(node_color, corner_color)

    def test_width_tight_after_merge(self):
        commits = [{'sha': 'm', 'parents': ['d', 'f']},
                   {'sha': 'f', 'parents': ['d']},
                   {'sha': 'd', 'parents': ['e']},
                   {'sha': 'e', 'parents': []}]
        g = build_graph(commits)
        self.assertEqual(len(g[0]['cells']), 3)                # merge — 2 лейна → 3 клетки
        self.assertEqual(len(g[-1]['cells']), 1)               # линейный хвост — 1 клетка


if __name__ == '__main__':
    unittest.main()
