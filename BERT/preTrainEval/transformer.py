import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import math, copy, time
from torch.autograd import Variable
import matplotlib.pyplot as plt


# import seaborn
# seaborn.set_context(context="talk")


class EncoderDecoder(nn.Module):
	"""
	A standard Encoder-Decoder architecture. Base for this and many other models
	"""

	def __init__(self, encoder, decoder, src_embed, tgt_embed, generator):
		super(EncoderDecoder, self).__init__()
		self.encoder = encoder
		self.decoder = decoder
		self.src_embed = src_embed
		self.tgt_embed = tgt_embed
		self.generator = generator

	def forward(self, src, tgt, src_mask, tgt_mask):
		return self.decode(self.encode(src, src_mask), src_mask, tgt, tgt_mask)

	def encode(self, src, src_mask):
		return self.encoder(self.src_embed(src), src_mask)

	def decode(self, memory, src_mask, tgt, tgt_mask):
		return self.decoder(self.tgt_embed(tgt), memory, src_mask, tgt_mask)


class Generator(nn.Module):
	"""
	define the standard linear+softmax generation step
	"""

	def __init__(self, d_model, vocab):
		super(Generator, self).__init__()
		self.proj = nn.Linear(d_model, vocab)

	def forward(self, x):
		return F.log_softmax(self.proj(x), dim=-1)


def clones(module, N):
	"""
	produce N identical layers
	:param module: 
	:param N: 
	:return: 
	"""
	return nn.ModuleList([copy.deepcopy(module) for _ in range(N)])


class Encoder(nn.Module):
	"""
	core encoder is a stack of N layers
	"""

	def __init__(self, layer, N):
		super(Encoder, self).__init__()
		self.layers = clones(layer, N)
		self.norm = LayerNorm(layer.size)

	def forward(self, x, mask):
		"""pass the input (and mask) through each layer in turn"""
		for layer in self.layers:
			x = layer(x, mask)
		return self.norm(x)


class LayerNorm(nn.Module):
	"""construct a layernorm module. 
	a residual connection around each of the two sub-layers, followed by layer normalization """
	def __init__(self, features, eps=1e-6):
		super(LayerNorm, self).__init__()
		self.a_2 = nn.Parameter(torch.ones(features))
		self.b_2 = nn.Parameter(torch.zeros(features))
		self.eps = eps

	def forward(self, x):
		mean = x.mean(-1, keepdim=True)
		std = x.std(-1, keepdim=True)
		return self.a_2 * (x - mean) / (std + self.eps) + self.b_2


class SublayerConnection(nn.Module):
	"""
	A residual connection followed by a layer norm.
	"""
	def __init__(self, size, dropout):
		super(SublayerConnection, self).__init__()
