import io
import unittest
from contextlib import redirect_stdout

import kittymock  # noqa: F401
from modules.overlay import mark_overlay


class MarkOverlayTest(unittest.TestCase):
    def test_emits_osc_setuservar_base64(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            mark_overlay('session')
        # c2Vzc2lvbg== = base64('session'); OSC 1337 SetUserVar, терминатор BEL
        self.assertEqual(buf.getvalue(),
                         '\033]1337;SetUserVar=cc_plugin=c2Vzc2lvbg==\007')

    def test_value_differs_per_plugin(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            mark_overlay('review')
        self.assertEqual(buf.getvalue(),
                         '\033]1337;SetUserVar=cc_plugin=cmV2aWV3\007')


if __name__ == '__main__':
    unittest.main()
