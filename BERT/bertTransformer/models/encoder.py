import math

import torch
import torch.nn as nn

from bertTransformer.models.neural import MultiHeadedAttention, PositionwiseFeedForward
from bertTransformer.models.rnn import LayerNormLSTM


class BertPooler(nn.Module):
	def __init__(self, hidden_size):
		super(BertPooler, self).__init__()
		self.dense = nn.Linear(hidden_size, hidden_size)
		self.activation = nn.Tanh()

	def forward(self, hidden_states):
		# We "pool" the model by simply taking the hidden state corresponding
		# to the first token.
		first_token_tensor = hidden_states[:, 0]
		pooled_output = self.dense(first_token_tensor)
		pooled_output = self.activation(pooled_output)
		return pooled_output


class PositionalEncoding(nn.Module):
	def __init__(self, dropout, dim, max_len=5000):
		pe = torch.zeros(max_len, dim)
		position = torch.arange(0, max_len).unsqueeze(1)
		div_term = torch.exp((torch.arange(0, dim, 2, dtype=torch.float) *
							  -(math.log(10000.0) / dim)))
		pe[:, 0::2] = torch.sin(position.float() * div_term)
		pe[:, 1::2] = torch.cos(position.float() * div_term)
		pe = pe.unsqueeze(0)
		super(PositionalEncoding, self).__init__()
		self.register_buffer('pe', pe)
		self.dropout = nn.Dropout(p=dropout)
		self.dim = dim

	def forward(self, emb, step=None):
		emb = emb * math.sqrt(self.dim)
		if (step):
			emb = emb + self.pe[:, step][:, None, :]

		else:
			emb = emb + self.pe[:, :emb.size(1)]
		emb = self.dropout(emb)
		return emb

	def get_emb(self, emb):
		return self.pe[:, :emb.size(1)]


class TransformerEncoderLayer(nn.Module):
	def __init__(self, d_model, heads, d_ff, dropout):
		super(TransformerEncoderLayer, self).__init__()

		self.self_attn = MultiHeadedAttention(
			heads, d_model, dropout=dropout)
		self.feed_forward = PositionwiseFeedForward(d_model, d_ff, dropout)
		self.layer_norm = nn.LayerNorm(d_model, eps=1e-6)
		self.dropout = nn.Dropout(dropout)

	def forward(self, iter, query, inputs, mask):
		if (iter != 0):
			input_norm = self.layer_norm(inputs)
		else:
			input_norm = inputs

		mask = mask.unsqueeze(1)
		context = self.self_attn(input_norm, input_norm, input_norm, mask=mask)
		out = self.dropout(context) + inputs
		return self.feed_forward(out)


class TransformerInterEncoder(nn.Module):
	def __init__(self, d_model, d_ff, heads, dropout, num_inter_layers=0, polarities_dim=3):
		super(TransformerInterEncoder, self).__init__()
		self.d_model = d_model
		self.num_inter_layers = num_inter_layers
		self.pos_emb = PositionalEncoding(dropout, d_model)
		self.transformer_inter = nn.ModuleList(
			[TransformerEncoderLayer(d_model, heads, d_ff, dropout)
			 for _ in range(num_inter_layers)])
		self.dropout = nn.Dropout(dropout)
		self.layer_norm = nn.LayerNorm(d_model, eps=1e-6)
		self.wo = nn.Linear(d_model, 1, bias=True)
		self.sigmoid = nn.Sigmoid()
		self.pooler = BertPooler(d_model)
		self.dense = nn.Linear(d_model, polarities_dim)

	def forward(self, out_name, top_vecs, mask):
		""" See :obj:`EncoderBase.forward()`"""

		batch_size, n_sents = top_vecs.size(0), top_vecs.size(1)
		pos_emb = self.pos_emb.pe[:, :n_sents]
		# if torch.cuda.is_available():
		# 	pos_emb = torch.cuda.FloatTensor(pos_emb).to(device)
		x = top_vecs * mask[:, :, None].float()
		x = x + pos_emb
		# x_ = x
		for i in range(self.num_inter_layers):
			x = self.transformer_inter[i](i, x, x, 1 - mask)  # all_sents * max_tokens * dim

		x = self.layer_norm(x)

		# sent_scores = self.sigmoid(self.wo(x))
		# sent_scores = sent_scores.squeeze(-1) * mask.float()
		if out_name == 'pool':
			x = self.pooler(x)
		if out_name == 'avg':
			x = torch.mean(x, dim=1)
		logits = self.dense(x)

		return logits  # sent_scores


class Classifier(nn.Module):
	def __init__(self, hidden_size):
		super(Classifier, self).__init__()
		self.linear1 = nn.Linear(hidden_size, 1)
		self.sigmoid = nn.Sigmoid()

	def forward(self, x, mask_cls):
		h = self.linear1(x).squeeze(-1)
		sent_scores = self.sigmoid(h) * mask_cls.float()
		return sent_scores


class RNNEncoder(nn.Module):
	def __init__(self, bidirectional, num_layers, input_size,
				 hidden_size, tag_size, dropout=0.0, batch_size=1):
		super(RNNEncoder, self).__init__()
		num_directions = 2 if bidirectional else 1
		assert hidden_size % num_directions == 0
		hidden_size = hidden_size // num_directions

		self.rnn = LayerNormLSTM(
			input_size=input_size,
			hidden_size=hidden_size,
			num_layers=num_layers,
			bidirectional=bidirectional)

		self.wo = nn.Linear(num_directions * hidden_size, 1, bias=True)
		self.dropout = nn.Dropout(dropout)
		self.sigmoid = nn.Sigmoid()
		self.att_weight = nn.Parameter(torch.randn(batch_size, 1, hidden_size))
		self.batch = batch_size
		self.hidden_dim = hidden_size
		self.tag_size = tag_size
		self.relation_embeds = nn.Embedding(self.tag_size, self.hidden_dim)

	def attention(self, H):  # input: (batch/1, hidden, seq); output: (batch/1, hidden, 1)
		M = torch.tanh(H)
		a = torch.nn.functional.softmax(torch.bmm(self.att_weight, M), 2)
		a = torch.transpose(a, 1, 2)
		return torch.bmm(H, a)

	def forward(self, out_name, x, mask):
		"""See :func:`EncoderBase.forward()`"""
		x = torch.transpose(x, 1, 0)
		memory_bank, _ = self.rnn(x)

		att_out = torch.tanh(self.attention(memory_bank.contiguous().view(self.batch, self.hidden_dim, -1)))
		# att_out = self.dropout_att(att_out)
		relation = torch.tensor([i for i in range(self.tag_size)], dtype=torch.long).repeat(self.batch, 1)
		if torch.cuda.is_available():
			relation = relation.cuda()
		relation = self.relation_embeds(relation)
		res = torch.add(torch.bmm(relation, att_out), self.relation_bias)
		res = torch.nn.functional.softmax(res, 1)

		return res.view(self.batch, -1)

		# memory_bank = self.dropout(memory_bank) + x
		# memory_bank = torch.transpose(memory_bank, 1, 0)
		#
		# sent_scores = self.sigmoid(self.wo(memory_bank))
		# sent_scores = sent_scores.squeeze(-1) * mask.float()
		# return sent_scores
