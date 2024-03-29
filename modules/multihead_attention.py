import torch
from torch import nn
from torch.nn import Parameter
import torch.nn.functional as F
import sys

# Code adapted from the fairseq repo.

class MultiheadAttention(nn.Module):
    """Multi-headed attention.
    See "Attention Is All You Need" for more details.
    """

    def __init__(self, embed_dim, num_heads, attn_dropout=0., input_dim=None):
        super().__init__()
        self.embed_dim = embed_dim
        self.input_dim = embed_dim if input_dim is None else input_dim
        self.num_heads = num_heads
        self.attn_dropout = attn_dropout
        self.head_dim = embed_dim // num_heads
        assert self.head_dim * num_heads == self.embed_dim, "embed_dim must be divisible by num_heads"
        self.scaling = self.head_dim ** -0.5

        self.in_proj_weight = Parameter(torch.Tensor(embed_dim+2*self.input_dim, embed_dim))
        self.in_proj_bias = Parameter(torch.Tensor(embed_dim*3))
        self.out_proj = nn.Linear(embed_dim, embed_dim)

        self.reset_parameters()

    def reset_parameters(self):
        nn.init.xavier_uniform_(self.in_proj_weight)
        nn.init.xavier_uniform_(self.out_proj.weight)
        nn.init.constant_(self.in_proj_bias, 0.)
        nn.init.constant_(self.out_proj.bias, 0.)

    def forward(self, query, key, value, attn_mask=None, reverse=False):
        """Input shape: Time x Batch x Channel
        Self-attention can be implemented by passing in the same arguments for
        query, key and value. Timesteps can be masked by supplying a T x T mask in the
        `attn_mask` argument. Padding elements can be excluded from
        the key by passing a binary ByteTensor (`key_padding_mask`) with shape:
        batch x src_len, where padding elements are indicated by 1s.
        """
        qkv_same = query.data_ptr() == key.data_ptr() == value.data_ptr()
        kv_same = key.data_ptr() == value.data_ptr()

        tgt_len, bsz, embed_dim = query.size()
        assert embed_dim == self.embed_dim
        assert list(query.size()) == [tgt_len, bsz, embed_dim]
        assert key.size() == value.size()

        if reverse:
            q = self.in_proj_k(query)
            k = self.in_proj_q(key)
            v = self.in_proj_v(key)
        else:
            q = self.in_proj_q(query)
            k, v = self.in_proj_kv(key)
        q = q * self.scaling

        # MHA TEST
        # s1 = torch.arange(320) + 1
        # s1 = s1.reshape(1, 10, 32)
        # s2 = s1.contiguous().view(tgt_len, 10 * self.num_heads, self.head_dim)
        # s3 = s2.transpose(0, 1)
        # s4 = s3.transpose(1, 2)

        # BMM TEST
        # s1 = torch.arange(32) + 1
        # s1 = s1.reshape(1, 4, 8)
        # s2 = s1.contiguous().view(1, 8, 4)
        # s2_1 = s2.transpose(0, 1)
        # s3 = torch.arange(64) + 1
        # s3 = s3.reshape(2, 4, 8)
        # s4 = s3.contiguous().view(2, 8, 4)
        # s4_1 = s4.transpose(0, 1)
        # s4_2 = s4_1.transpose(1, 2)
        # s5 = torch.bmm(s2_1, s4_2)


        q = q.contiguous().view(bsz * tgt_len, self.num_heads, self.head_dim).transpose(0, 1)   # edit by sy 1103
        if k is not None:
            k = k.contiguous().view(-1, self.num_heads, self.head_dim).transpose(0, 1)   # edit by sy 1103
        if v is not None:
            v = v.contiguous().view(-1, self.num_heads, self.head_dim).transpose(0, 1)   # edit by sy 1103

        src_len = k.size(1)

        attn_weights = torch.bmm(q, k.transpose(1, 2))
        assert list(attn_weights.size()) == [self.num_heads, bsz * tgt_len, src_len]   # edit by sy 1103

        if attn_mask is not None:
            try:
                attn_weights += attn_mask.unsqueeze(0)
            except:
                print(attn_weights.shape)
                print(attn_mask.unsqueeze(0).shape)
                assert False
                
        attn_weights = F.softmax(attn_weights.float(), dim=-1).type_as(attn_weights)
        # attn_weights = F.relu(attn_weights)
        # attn_weights = attn_weights / torch.max(attn_weights)
        attn_weights = F.dropout(attn_weights, p=self.attn_dropout, training=self.training)

        attn = torch.bmm(attn_weights, v)
        assert list(attn.size()) == [self.num_heads, bsz * tgt_len, self.head_dim]   # edit by sy 1103

        attn = attn.transpose(0, 1).contiguous().view(tgt_len, bsz, embed_dim)
        attn = self.out_proj(attn)

        # average attention weights over heads
        attn_weights = attn_weights.view(bsz, self.num_heads, tgt_len, src_len)
        attn_weights = attn_weights.sum(dim=1) / self.num_heads
        return attn, attn_weights

    def in_proj_kv(self, key):
        return self._in_proj(key, start=self.embed_dim, startb=self.embed_dim).chunk(2, dim=-1)

    def in_proj_q(self, query, **kwargs):
        return self._in_proj(query, end=self.embed_dim, endb=self.embed_dim, **kwargs)

    def in_proj_k(self, key):
        return self._in_proj(key, start=self.embed_dim, end=self.embed_dim+self.input_dim,
                                  startb=self.embed_dim, endb=self.embed_dim*2)

    def in_proj_v(self, key):
        return self._in_proj(key, start=self.embed_dim+self.input_dim, end=self.embed_dim+self.input_dim*2,
                                  startb=self.embed_dim*2, endb=self.embed_dim*3)

    def _in_proj(self, input, start=0, end=None, startb=0, endb=None, **kwargs):
        weight = kwargs.get('weight', self.in_proj_weight)
        bias = kwargs.get('bias', self.in_proj_bias)
        weight = weight[start:end, :]
        bias = bias[startb:endb]
        if bias.shape[0]!=weight.shape[0]: weight=weight.reshape(-1,self.embed_dim*2).T # KV
        return F.linear(input, weight, bias)
