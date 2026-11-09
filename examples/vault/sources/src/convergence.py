"""Observed order from solutions on three grids (demo dummy)."""
from math import log2


def observed_order(u_h, u_h2, u_h4):
    return log2((u_h - u_h2) / (u_h2 - u_h4))
