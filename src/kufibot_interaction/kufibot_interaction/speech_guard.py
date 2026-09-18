"""Pre-playback detection of internal workflow-control leakage.

Node instructions can overlap with a perfectly valid spoken answer (for
example, an opening instruction that asks the robot to introduce itself).
Never reject ordinary model text merely because it resembles that instruction:
doing so terminates the entire voice session.  We only block unambiguously
internal control language here.
"""
import re


def _words(text):
    return re.findall(r'\w+', text.replace('İ', 'i').casefold().replace('ı', 'i'))


def validate_speech(text, messages):
    words = _words(text)
    normalized = ' '.join(words)
    internal = ('etkin node talimatina göre', 'görüşme başladi etkin node',
                'yalniz json karar üret', 'sistem talimatlari senin davranişini belirler')
    leaked = any(marker in normalized for marker in internal)
    if leaked:
        raise ValueError('Model sistem talimatını yanıt olarak tekrarladı; seslendirme engellendi. '
                         'Talimat takibi daha iyi bir sohbet modeli seçin.')
