"""Compose original MuJoCo RGB evidence; no synthetic objects or annotations on RGB."""
from pathlib import Path
from PIL import Image, ImageDraw
HERE=Path(__file__).resolve().parents[1]
ROOT=HERE.parents[2]/'outputs/scene_datasets/scene-val103-cup30-minimal-v3-20261012-r2'
for sample in sorted((ROOT/'samples').iterdir()):
 case='Case1' if 'case1' in sample.name else 'Case2'
 images=[Image.open(sample/f'media/{stage}_oblique_a.png').convert('RGB') for stage in ('before','after')]
 w,h=images[0].size
 panel=Image.new('RGB',(2*w,h+38),'white'); draw=ImageDraw.Draw(panel)
 for i,img in enumerate(images):
  panel.paste(img,(i*w,38));draw.text((12+i*w,12),f'{case}: '+('BEFORE' if i==0 else 'AFTER'),fill='black')
 panel.save(HERE/f'figures/{case.lower()}-paired.png')
 print(case, (w,h), sample)
