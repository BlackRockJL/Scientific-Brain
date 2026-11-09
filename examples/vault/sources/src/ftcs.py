"""Explicit FTCS step for u_t = alpha u_xx (demo dummy)."""


def step(u, r):
    return [u[0]] + [u[i] + r * (u[i - 1] - 2 * u[i] + u[i + 1]) for i in range(1, len(u) - 1)] + [u[-1]]
