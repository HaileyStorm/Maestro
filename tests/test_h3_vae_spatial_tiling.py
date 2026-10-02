"""CPU parity against the H3 reference's buffered spatial tile assembly."""
import sys
import unittest
from pathlib import Path

import torch
from torch import nn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from models.minimax_h3.video_vae import AutoencoderKLMiniMaxH3


class TileContextDecoder(nn.Module):
    """Make each decoded tile depend on its own context, as learned tiles do."""

    def forward(self, tile):
        pixels = tile.repeat_interleave(2, dim=-2).repeat_interleave(2, dim=-1)
        return pixels + tile.mean(dim=(-2, -1), keepdim=True)


def make_vae(tile_size, overlap, *, tiling=True):
    # Exercise the real tile code without allocating learned parameters.
    vae = object.__new__(AutoencoderKLMiniMaxH3)
    nn.Module.__init__(vae)
    vae.decoder = TileContextDecoder()
    vae.post_quant_conv = nn.Identity()
    vae.use_tiling = tiling
    vae.spatial_compression_ratio = 2
    vae.tile_sample_min_height = tile_size
    vae.tile_sample_min_width = tile_size
    vae.tile_sample_min_overlap_height = overlap
    vae.tile_sample_min_overlap_width = overlap
    return vae


def buffered_reference(vae, latents):
    height, width = (size * 2 for size in latents.shape[-2:])
    ys, yl, yo = vae._split_tiles(height, vae.tile_sample_min_height,
                                 vae.tile_sample_min_overlap_height)
    xs, xl, xo = vae._split_tiles(width, vae.tile_sample_min_width,
                                 vae.tile_sample_min_overlap_width)
    tiles = [[vae.decoder(vae.post_quant_conv(latents[
        ..., y // 2:(y + y_length) // 2, x // 2:(x + x_length) // 2]))
        for x, x_length in zip(xs, xl)] for y, y_length in zip(ys, yl)]
    return vae._stitch_tiles(tiles, yo, xo)


class H3SpatialTilingTests(unittest.TestCase):
    def test_streaming_matches_reference_original_edges(self):
        generator = torch.Generator().manual_seed(341)
        # Single tile; horizontal/vertical seams; corner; three-way overlap.
        cases = [(8, 8, 12, 4), (8, 30, 12, 4), (30, 8, 12, 4),
                 (22, 30, 12, 4), (14, 14, 10, 8)]
        for dtype in (torch.float32, torch.float16):
            for height, width, tile_size, overlap in cases:
                with self.subTest(dtype=dtype, canvas=(height, width)):
                    vae = make_vae(tile_size, overlap)
                    latents = torch.randn(2, 3, 2, height // 2, width // 2,
                                          generator=generator).to(dtype)
                    original = latents.clone()
                    expected = buffered_reference(vae, latents)
                    actual = vae._decode_clip(latents)
                    self.assertEqual(tuple(actual.shape), (2, 3, 2, height, width))
                    torch.testing.assert_close(actual, expected, rtol=0, atol=0)
                    torch.testing.assert_close(latents, original, rtol=0, atol=0)

    def test_disabled_tiling_uses_full_decoder(self):
        vae = make_vae(12, 4, tiling=False)
        latents = torch.arange(18, dtype=torch.float32).reshape(1, 1, 2, 3, 3)
        torch.testing.assert_close(vae._decode_clip(latents),
                                   vae.decoder(vae.post_quant_conv(latents)),
                                   rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
