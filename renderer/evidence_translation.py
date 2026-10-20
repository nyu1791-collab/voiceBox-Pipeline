"""Display faithful source translations separately from spoken captions."""
from PIL import Image, ImageDraw, ImageFont
from subtitle_tools import wrap_japanese


class TranslationCards:
    def __init__(self, visuals, layout, font_path):
        self.pages = {}
        self.cards = {}
        x1, _, x2, bottom = layout['zones']['evidence']
        self.xy = (x1, bottom + 8)
        self.width = x2 - x1
        self.height = 124
        if self.xy[1] + self.height > 490:
            if any(v.get('translation') for v in visuals):
                raise ValueError('Leave room below evidence for the translation, ending before y=490')
        self.font = ImageFont.truetype(str(font_path), 28)
        self.label_font = ImageFont.truetype(str(font_path), 20)
        for visual in visuals:
            text = visual.get('translation', '')
            if not isinstance(text, str):
                raise ValueError('translation must be a Japanese string')
            if not text:
                continue
            lines = wrap_japanese(text, self.font, self.width - 28, strict=True)
            if not lines or ''.join(lines) != text:
                raise ValueError('Translation cannot fit without changing its text')
            self.pages[visual['id']] = [lines[i:i+3] for i in range(0, len(lines), 3)]

    def state(self, visual_id, elapsed, duration):
        pages = self.pages.get(visual_id, [])
        if not pages:
            return None
        index = min(len(pages)-1, max(0, int(max(0, elapsed) / max(.001, duration) * len(pages))))
        return visual_id, index

    def draw(self, frame, state):
        if state is None:
            return
        if state not in self.cards:
            visual_id, index = state
            pages = self.pages[visual_id]
            card = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(card)
            draw.rounded_rectangle((0, 0, self.width-1, self.height-1), radius=12,
                                   fill='#F5F8FF', outline='#7D98BA', width=2)
            label = '日本語訳' + (f'  {index+1}/{len(pages)}' if len(pages) > 1 else '')
            draw.text((14, 2), label, font=self.label_font, fill='#345577')
            for i, line in enumerate(pages[index]):
                draw.text((14, 27+i*30), line, font=self.font, fill='#17202F')
            self.cards[state] = card
        frame.paste(self.cards[state], self.xy, self.cards[state])
