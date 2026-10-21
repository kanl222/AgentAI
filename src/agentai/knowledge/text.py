"""Lexical features shared by ranking and question interpretation."""
import re

def tokenize(text):
    return re.findall(r'\w+',text.lower()) or ['_']


def lexical_tokens(text):
    """Retain exact identifiers and add Russian prefix features for inflected words."""
    words = tokenize(text)
    return words + ['ru:'+word[:6] for word in words if len(word)>=7 and re.fullmatch('[а-яё]+',word)]
