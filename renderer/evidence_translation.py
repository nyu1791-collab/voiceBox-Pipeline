"""Keep the complete faithful translation directly below its source screenshot."""
from PIL import Image, ImageDraw, ImageFont
from subtitle_tools import wrap_japanese


class TranslationCards:
    def __init__(self, visuals, layout, font_path):
        self.pages, self.cards, self.plans = {}, {}, {}
        x1, top, x2, old_bottom = layout['zones']['evidence']
        self.default_region = (x1, top, x2, old_bottom)
        self.width = x2-x1
        settings = layout.get('evidence_translation', {})
        preferred, minimum = settings.get('font_size', 24), settings.get('min_font_size', 20)
        if type(preferred) is not int or type(minimum) is not int or not 20 <= minimum <= preferred <= 28:
            raise ValueError('Translation fonts must stay within the approved 20..28px range')
        # These are source notes. Narration captions retain 36px and their own space.
        bottom = min(490, layout['zones'].get('caption', [0,498])[1]-8)
        maximum = min(220, bottom-top-168-8)
        self.font = ImageFont.truetype(str(font_path), preferred)
        self.label_font = ImageFont.truetype(str(font_path), 18)
        for visual in visuals:
            text = visual.get('translation', '')
            if not isinstance(text, str):
                raise ValueError('translation must be a Japanese string')
            if not text:
                continue
            chosen = None
            for size in range(preferred, minimum-1, -1):
                face = ImageFont.truetype(str(font_path), size)
                step = size+4
                max_lines = int((maximum-37)//step)
                # Reject impossible text before the phrase fitter measures many candidate wraps.
                if face.getlength(text) > (self.width-28+size)*max_lines:
                    continue
                lines = wrap_japanese(text, face, self.width-28, strict=True)
                height = 29+len(lines or [])*step+8
                if lines and ''.join(lines) == text and height <= maximum:
                    chosen = (face, lines, height, step)
                    break
            if chosen is None:
                raise ValueError('Full translation does not fit: capture a shorter source excerpt; do not page or truncate it')
            face, lines, height, step = chosen
            xy = (x1, bottom-height)
            self.pages[visual['id']] = [lines]  # One stable full-text card, never timed pages.
            self.plans[visual['id']] = {'xy': xy, 'height': height, 'font': face,
                'lines': lines, 'line_step': step, 'text': text,
                'evidence_region': (x1, top, x2, xy[1]-8)}

    def evidence_region(self, visual_id):
        return self.plans.get(visual_id, {}).get('evidence_region', self.default_region)

    def state(self, visual_id, elapsed=0, duration=1):
        return (visual_id, 0) if visual_id in self.plans else None

    def draw(self, frame, state):
        if state is None:
            return
        if state not in self.cards:
            plan = self.plans[state[0]]
            card = Image.new('RGBA', (self.width, plan['height']))
            draw = ImageDraw.Draw(card)
            draw.rounded_rectangle((0, 0, self.width-1, plan['height']-1), radius=10,
                                   fill='#F5F8FF', outline='#7D98BA', width=2)
            draw.text((14, 3), '日本語訳（全文）', font=self.label_font, fill='#345577', anchor='lt')
            for i, line in enumerate(plan['lines']):
                draw.text((14, 28+i*plan['line_step']), line, font=plan['font'], fill='#17202F', anchor='lt')
            self.cards[state] = card
        xy = self.plans[state[0]]['xy']
        frame.paste(self.cards[state], xy, self.cards[state])

    def report(self):
        return {vid: {'mode': 'full', 'pages': 1, 'characters': len(p['text']),
            'font_size': p['font'].size, 'lines': len(p['lines']),
            'translation_region': [*p['xy'], p['xy'][0]+self.width, p['xy'][1]+p['height']],
            'evidence_region': list(p['evidence_region'])} for vid,p in self.plans.items()}
