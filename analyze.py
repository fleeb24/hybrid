
import sys, os, time
import shutil
# from IPython.core.interactiveshell import InteractiveShell
# InteractiveShell.ast_node_interactivity = "all"
from IPython import display
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.distributions as distrib
import torch.multiprocessing as mp
import torchvision.models
import torchvision
from torch.utils.data import Dataset, DataLoader, TensorDataset
import gym
import numpy as np
# %matplotlib tk
import matplotlib.pyplot as plt
import pickle
from tqdm import tqdm
import imageio
import seaborn as sns
from collections import OrderedDict
from PIL import Image, ImageDraw, ImageFont
#plt.switch_backend('Qt5Agg') #('Qt5Agg')
import foundation as fd
from foundation import models
from foundation import util
from foundation import train
from foundation import train as trn

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import style
import seaborn as sns

from matplotlib import animation

#from foundation.util import replicate, Cloner

import evaluate as dis_eval
from hybrid import get_model, get_data

from run_fid import compute_inception_stat, load_inception_model, compute_frechet_distance


def show_nums(imgs, titles=None, H=None, W=None, figsize=(6, 6),
			  reverse_rows=False, grdlines=False):
	# if H is None and W is None:
	# 	B = imgs.size(0)
	# 	l = int(np.sqrt(B))
	# 	assert l ** 2 == B, 'not right: {} {}'.format(l, B)
	# 	H, W = l, l
	# elif H is None:
	# 	H = imgs.shape[0] // W
	# elif W is None:
	# 	W = imgs.shape[0] // H
	H,W = util.calc_tiling(imgs.size(0), H=H, W=W)

	imgs = imgs.cpu().permute(0, 2, 3, 1).squeeze().numpy()

	fig, axes = plt.subplots(H, W, figsize=figsize)

	if titles is None:
		titles = [None] * len(imgs)

	iH, iW = imgs.shape[1], imgs.shape[2]

	for ax, img, title in zip(axes.flat, imgs, titles):
		plt.sca(ax)
		if reverse_rows:
			img = img[::-1]
		plt.imshow(img)
		if grdlines:
			plt.plot([0, iW], [iH / 2, iH / 2], c='r', lw=.5, ls='--')
			plt.plot([iW / 2, iW / 2], [0, iH], c='r', lw=.5, ls='--')
			plt.xlim(0, iW)
			plt.ylim(0, iH)
		if title is not None:
			plt.xticks([])
			plt.yticks([])
			plt.title(title)
		else:
			plt.axis('off')
	#     fig.tight_layout()
	return fig


def update_checkpoint(S, *keys, overwrite=False):

	if 'ckpt' not in S:
		return None

	checkpoint = S.ckpt

	if 'analysis' not in checkpoint['records']:
		checkpoint['records']['analysis'] = {}



	storage = checkpoint['records']['analysis']

	updated = []

	for k in keys:
		if overwrite or k not in storage:
			updated.append(k)
			storage[k] = S[k]


	print('Updated {} keys: {}'.format(len(updated), updated))

	if len(updated) > 0:
		# dest = S.ckpt_path + '-backup'
		# shutil.copy(S.ckpt_path, dest)
		# print('WARNING: just backed up checkpoint to: {}'.format(dest))
		torch.save(checkpoint, S.ckpt_path)
		print('Saved updated checkpoint to: {}'.format(S.ckpt_path))


def load_fn(S, fast=False, **unused):

	cpath = S.ckpt_path
	A = S.A if 'A' in S else train.get_config()

	A.dataset.device = 'cpu'

	if fast:
		print('Fast: loading only model')

	out = train.load(path=cpath, A=A, get_model=get_model, get_data=None if fast else get_data,
												   update_config=True,
												   return_args=True, return_ckpt=True)

	if fast:
		S.A, S.model, S.ckpt = out
		return
	A, (dataset, *other), model, ckpt = out


	if 'trainset' not in S:
		S.trainset = dataset
	if 'valset' not in S:
		S.valset = None

		if len(other) and other[0] is not None:
			print('*** Using validation set')
			S.valset = other[0]
			dataset = S.valset

	model.eval()

	if 'dataset' not in S:
		S.dataset = dataset
	else:
		print('Found an existing dataset, len={}, using that instead'.format(len(S.dataset)))

	S.A = A
	S.other = other
	S.model = model
	S.ckpt = ckpt

	records = ckpt['records']
	print('Trained on {:2.2f} M samples'.format(records['total_samples']['train'] / 1e6))
	# if len(other) and other[0] is not None:
	# 	dataset = other[0]
	# 	print('Using validation set')
	# else:
	# 	print('Using training set')


	S.records = records

	if 'analysis' in S.records:
		print('Found extra results from previous analysis')

		if 'skip_precomputed' not in A or not A.skip_precomputed:

			for k,v in S.records['analysis'].items():
				if k not in S:
					print('Using precomputed {}'.format(k))
					S[k] = v
		else:
			print('Skipping all precomputed values')





def run_model(S, pbar=None, **unused):
	A = S.A
	dataset = S.dataset
	model = S.model

	assert False, 'unused'

	if 'loader' not in S:
		# DataLoader

		A.dataset.batch_size = 16

		util.set_seed(0)
		loader = train.get_loaders(dataset, batch_size=A.dataset.batch_size, num_workers=A.num_workers,
								   shuffle=True, drop_last=False, )
		util.set_seed(0)
		loader = iter(loader)

		S.loader = loader

	common_Ws = {64: 8, 32: 4, 16: 4, 9: 3, 8: 2, 4: 2}
	border, between = 0.02, 0.01
	img_W = common_Ws[A.dataset.batch_size]
	S.img_W = img_W
	S.border, S.between = border, between



	if 'X' not in S:
		# Batch

		batch = next(loader)
		batch = util.to(batch, A.device)

		S.batch = batch
		S.X = batch[0]

	X = S.X

	with torch.no_grad():

		if model.enc is None:
			q = model.sample_prior(X.size(0))
		else:
			q = model.encode(X)
		qdis = None
		qmle = q
		if isinstance(q, distrib.Distribution):
			qdis = q
			q = q.rsample()
			qmle = qdis.loc
		rec = model.decode(q)
		# vrec = model.disc(rec) if model.disc is not None else None

		p = model.sample_prior(X.size(0))
		gen = model.decode(p)

		h = util.shuffle_dim(q)
		hyb = model.decode(h)

	S.rec = rec
	S.gen = gen
	S.hyb = hyb

	S.q = q
	S.p = p
	S.h = h

	S.qdis = qdis
	S.qmle = qmle

	batch_size = 128  # number of samples to get distribution
	util.set_seed(0)
	int_batch = next(iter(train.get_loaders(dataset, batch_size=batch_size, num_workers=A.num_workers,
											shuffle=True, drop_last=False, )))
	with torch.no_grad():
		int_batch = util.to(int_batch, A.device)
		int_X, = int_batch
		if model.enc is None:
			int_q = model.sample_prior(int_X.size(0))
		else:
			int_q = model.encode(int_X)
		dis_int_q = None
		if isinstance(int_q, distrib.Distribution):
			#         int_q = int_q.rsample()
			dis_int_q = int_q
			int_q = int_q.loc
	del int_batch
	del int_X

	S.int_q = int_q
	S.dis_int_q = dis_int_q


	latent_dim = A.model.latent_dim
	iH, iW = X.shape[-2:]

	rows = 4
	steps = 60

	if 'bounds' in S:
		bounds = S.bounds
	else:
		# bounds = -2,2
		bounds = None
	# dlayout = rows, latent_dim // rows
	dlayout = util.calc_tiling(latent_dim)
	outs = []

	all_diffs = []
	inds = [0, 2, 3]
	inds = np.arange(len(q))
	save_inds = [0, 1, 2, 3]
	# save_inds =  []

	saved_walks = []

	for idx in inds:

		walks = []
		for dim in range(latent_dim):

			dev = int_q[:, dim].std()
			if bounds is None:
				deltas = torch.linspace(int_q[:, dim].min(), int_q[:, dim].max(), steps)
			else:
				deltas = torch.linspace(bounds[0], bounds[1], steps)
			vecs = torch.stack([int_q[idx]] * steps)
			vecs[:, dim] = deltas

			with torch.no_grad():
				walks.append(model.decode(vecs).cpu())

		walks = torch.stack(walks, 1)
		chn = walks.shape[2]

		dsteps = 10
		diffs = (walks[dsteps:] - walks[:-dsteps]).abs()  # .view(steps-dsteps, latent_dim, chn, 64*64)
		#     diffs /= (walks[dsteps:]).abs()
		# diffs = diffs.clamp(min=1e-10,max=1).abs()
		diffs = diffs.view(steps - dsteps, latent_dim, chn * iH * iW).mean(-1)
		#     diffs = 1 - diffs.mean(-1)
		#     print(diffs.shape)
		#     diffs *= 2
		all_diffs.append(diffs.mean(0))
		#     print(all_diffs[-1])

		if idx in save_inds:
			# save_dir = S.save_dir

			walks_full = walks.view(steps, dlayout[0], dlayout[1], chn, iH, iW) \
				.permute(0, 1, 4, 2, 5, 3).contiguous().view(steps, iH * dlayout[0], iW * dlayout[1], chn).squeeze()
			images = []
			for img in walks_full.cpu().numpy():
				images.append((img * 255).astype(np.uint8))

			saved_walks.append(images)

			# imageio.mimsave(os.path.join(save_dir, 'walks-idx{}.gif'.format(idx, dim)), images)
			#
			# with open(os.path.join(save_dir, 'walks-idx{}.gif'.format(idx, dim)), 'rb') as f:
			# 	outs.append(display.Image(data=f.read(), format='gif'))
			del walks_full

	all_diffs = torch.stack(all_diffs)

	S.all_diffs = all_diffs
	S.saved_walks = saved_walks

	dataset = S.dataset

	full_q = None

	if model.enc is not None:

		N = min(1000, len(dataset))

		print('Using {} samples'.format(N))

		assert (N//4)*4 == N, 'invalid num: {}'.format(N)

		loader = train.get_loaders(dataset, batch_size=N//4, num_workers=A.num_workers,
												shuffle=True, drop_last=False, )

		util.set_seed(0)

		if pbar is not None:
			loader = pbar(loader, total=len(loader))
			loader.set_description('Collecting latent vectors')

		full_q = []

		for batch in loader:
			batch = util.to(batch, A.device)
			X, = batch

			with torch.no_grad():

				q = model.encode(X)
				if isinstance(q, distrib.Distribution):
					q = torch.stack([q.loc, q.scale], 1)
				full_q.append(q.cpu())

		if pbar is not None:
			loader.close()

		if len(full_q):
			full_q = torch.cat(full_q)

			# print(full_q.shape)

			if len(full_q.shape) > 2:
				full_q = distrib.Normal(loc=full_q[:,0], scale=full_q[:,1])

		else:
			full_q = None

	S.full_q = full_q

	if full_q is not None:
		if 'results' not in S:
			S.results = {}
		S.results['val_Q'] = full_q

		print('Storing {} latent vectors'.format(len(full_q if not isinstance(full_q, distrib.Distribution)
													 else full_q.loc)))




def gen_prior(model, N):
	return model.generate(N)


def gen_target(model, X=None, Q=None, hybrid=False, ret_q=False):
	if Q is None:
		assert X is not None

		Q = model.encode(X)
		if isinstance(Q, distrib.Distribution):
			Q = Q.mean

	if hybrid:
		Q = util.shuffle_dim(Q)

	gen = model.decode(Q)

	if ret_q:
		return gen, Q
	return gen


def new_loader(dataset, batch_size, shuffle=False):
	return trn.get_loaders(dataset, batch_size=batch_size, shuffle=shuffle)


def gen_batch(dataset, N=None, loader=None, shuffle=False, seed=None, ret_loader=False):
	if seed is not None:
		util.set_seed(seed)

	if loader is None:
		assert N is not None
		loader = iter(new_loader(dataset, batch_size=N, shuffle=shuffle))

	try:
		batch = util.to(next(loader), 'cuda')
		B = batch.size(0)
	except StopIteration:
		pass
	else:
		if N is None or B == N:
			if ret_loader:
				return batch[0], loader
			return batch[0]

	loader = iter(new_loader(dataset, batch_size=N, shuffle=shuffle))
	batch = util.to(next(loader), 'cuda')

	if ret_loader:
		return batch[0], loader

	return batch[0]


def compute_all_fid_scores(model, dataset, fid_stats_ref_path, fid=None):
	if fid is None:
		fid = {'scores': {}, 'stats': {}}

	path = os.path.join(os.environ["FOUNDATION_DATA_DIR"], fid_stats_ref_path)
	f = pickle.load(open(path, 'rb'))
	ref_stats = f['m'][:], f['sigma'][:]

	inception = load_inception_model(dim=2048, device='cuda')

	n_samples = 50000
	# n_samples = 100 # testing

	# rec
	name = 'rec'
	if name not in fid['scores']:
		util.set_seed(0)
		loader = None

		def _generate(N):
			nonlocal loader
			X, loader = gen_batch(dataset, loader=loader, shuffle=True, N=N, ret_loader=True)
			return gen_target(model, X=X, hybrid=False)

		stats = compute_inception_stat(_generate, inception=inception, pbar=tqdm, n_samples=n_samples)

		fid['scores'][name] = compute_frechet_distance(*stats, *ref_stats)
		fid['stats'][name] = stats
	print('FID-rec: {:.2f}'.format(fid['scores'][name]))

	# hyb
	name = 'hyb'
	if name not in fid['scores']:
		util.set_seed(0)
		loader = None

		def _generate(N):
			nonlocal loader
			X, loader = gen_batch(dataset, loader=loader, shuffle=True, N=N, ret_loader=True)
			return gen_target(model, X=X, hybrid=True)

		stats = compute_inception_stat(_generate, inception=inception, pbar=tqdm, n_samples=n_samples)

		fid['scores'][name] = compute_frechet_distance(*stats, *ref_stats)
		fid['stats'][name] = stats
	print('FID-hybrid: {:.2f}'.format(fid['scores'][name]))

	# prior
	name = 'prior'
	if name not in fid['scores']:
		util.set_seed(0)

		def _generate(N):
			return gen_prior(model, N)

		stats = compute_inception_stat(_generate, inception=inception, pbar=tqdm, n_samples=n_samples)

		fid['scores'][name] = compute_frechet_distance(*stats, *ref_stats)
		fid['stats'][name] = stats
	print('FID-prior: {:.2f}'.format(fid['scores'][name]))

	return fid


_disent_eval_fns = {
	'IRS': dis_eval.eval_irs,
		'MIG': dis_eval.eval_mig, # testing
		'DCI': dis_eval.eval_dci,
		'SAP': dis_eval.eval_sap,
		'ModExp': dis_eval.eval_modularity_explicitness,
		'Unsup': dis_eval.eval_unsupervised,

		'bVAE': dis_eval.eval_beta_vae,
		'FVAE': dis_eval.eval_factor_vae,
}


def compute_all_disentanglement(model, disent=None):
	if disent is None:
		disent = {}

	dataset = dis_eval.shapes3d.Shapes3D()
	repr_fn = dis_eval.representation_func(model, 'cuda')

	itr = tqdm(_disent_eval_fns.items(), total=len(_disent_eval_fns))

	for name, eval_fn in itr:
		itr.set_description(name)
		if name not in disent:
			disent[name] = eval_fn(model='', representation_function=repr_fn, dataset=dataset, seed=0)
		print('{}: {}'.format(name, disent[name]))

	return disent


def get_traversal_vecs(Q, steps=32, bounds=None, mnmx=None):

	N, D = Q.shape
	S = steps
	#
	# dH, dW = util.calc_tiling(D)
	#
	# # bounds = (-2,2)
	#
	# save_inds = [0, 1, 2, 3]
	#
	# saved_walks = []

	I = torch.eye(D).view(1, 1, D, D)

	deltas = torch.linspace(0, 1, steps=S)
	deltas = torch.stack([deltas] * D)  # DxS

	if mnmx is None:
		if bounds is None:
			mnmx = (Q.min(0)[0].view(D, 1), Q.max(0)[0].view(D, 1))
		else:
			mnmx = torch.ones(D)*bounds[0], torch.ones(D)*bounds[1]

	mn, mx = mnmx
	mn, mx = mn.view(D, 1), mx.view(D, 1)

	deltas *= mx - mn
	deltas += mn
	deltas = deltas.t().unsqueeze(0).expand(N, S, D).unsqueeze(-1)

	Q = Q.unsqueeze(1).unsqueeze(-1).expand(N, S, D, D)

	vecs = Q * (1 - I) + deltas * I
	vecs = vecs.permute(0, 3, 1, 2) # NxDxSxD (batch, which dim, steps, vec)

	return vecs

def get_traversals(vecs, model, pbar=None): # last dim must be latent dim (model input)

	*shape, D = vecs.shape

	dataset = TensorDataset(vecs.view(-1,D))

	loader = trn.get_loaders(dataset, batch_size=64, shuffle=False)

	if pbar is not None:
		loader = pbar(loader)

	imgs = []

	for Q, in loader:
		with torch.no_grad():
			Q = Q.to('cuda')
			imgs.append(gen_target(model, Q=Q, hybrid=False).cpu())

	imgs = torch.cat(imgs)

	_, *img_shape = imgs.shape

	return imgs.view(*shape,*img_shape)

def get_traversal_anim(frames, vals=None, text_fmt='{:2.2f}', text_size=12, scale=1, fps=20):

	frames = frames.permute(0,2,3,1).cpu().numpy()
	if vals is not None:
		vals = vals.cpu().numpy()

	H, W, C = frames[0].shape
	asp = W/H
	fig = plt.figure(figsize=(asp, 1), dpi=int(H*scale),)

	ax = plt.axes([0, 0, 1, 1], frameon=False)
	ax.get_xaxis().set_visible(False)
	ax.get_yaxis().set_visible(False)
	plt.autoscale(tight=True)

	im = plt.imshow(frames[0])
	# plt.axis('off')
	# plt.tight_layout()
	if vals is not None:
		txt = plt.text(5,text_size*H//64, text_fmt.format(vals[0]), size=text_size)
	pass

	plt.close()

	def init():
		im.set_data(frames[0])
		if vals is not None:
			txt.set_text(text_fmt.format(vals[0]))

	def animate(i):
		im.set_data(frames[i])
		if vals is not None:
			txt.set_text(text_fmt.format(vals[i]))
		return im

	anim = animation.FuncAnimation(fig, animate, init_func=init, frames=len(frames), interval=1000//fps)
	return anim


def viz_latent(Q, figax=None, figsize=(9, 3), lim_y=None):
	Xs = np.arange(Q.shape[-1]) + 1
	inds = np.stack([Xs] * Q.shape[0])

	vals = Q.cpu().numpy()
	df = pd.DataFrame({'x': inds.reshape(-1), 'y': vals.reshape(-1)})

	if figax is None:
		figax = plt.subplots(figsize=figsize)
	fig, ax = figax

	# plt.figure(fig.num)
	plt.sca(ax)

	hue = None
	split = False
	color = 'C0'
	inner = 'box'
	palette = None

	sns.violinplot(x='x', y='y', hue=hue,
	               data=df, split=split, color=color, palette=palette,
	               scale="count", inner=inner, gridsize=100, )
	if lim_y is not None:
		plt.ylim(-lim_y, lim_y)
	plt.title('Distributions of Latent Dimensions')
	plt.xlabel('Dimension')
	plt.ylabel('Values')
	plt.tight_layout()
	return fig, ax


def compute_diffs_old(walks, dsteps=10):
	'''
	computes the L2 distance in pixel space between an image and the image where
	a single latent dimension is perturbed by approximately half of the range
	of that latent dim
	'''

	B, D, S, C, H, W = walks.shape

	diffs = (walks[:, :, dsteps:] - walks[:, :, :-dsteps]).pow(2)
	diffs = diffs.view(B, D, (S - dsteps) * C * H * W).mean(-1) * C
	return diffs


def compute_diffs(walks, dsteps=10):
	'''
	computes the Linf distance in pixel space between an image and the image where
	a single latent dimension is perturbed by approximately half of the range
	of that latent dim
	'''

	B, D, S, C, H, W = walks.shape

	diffs = (walks[:, :, dsteps:] - walks[:, :, :-dsteps]).abs().max(-3)[0]
	diffs = diffs.view(B, D, (S - dsteps) * H * W).mean(-1)
	return diffs


def viz_interventions(dists, figax=None, figsize=(9, 3), color='C2'):

	vals = dists.cpu().numpy()
	Xs = np.arange(vals.shape[-1]) + 1
	inds = np.stack([Xs] * vals.shape[0])
	df = pd.DataFrame({'x': inds.reshape(-1), 'y': vals.reshape(-1)})
	# df['moment']='log(sigma)'

	hue = None
	split = False
	# color = 'C2'
	inner = 'box'
	palette = None

	if figax is None:
		figax = plt.subplots(figsize=figsize)
	fig, ax = figax
	plt.sca(ax)
	sns.violinplot(x='x', y='y', hue=hue,
	               data=df, split=split, color=color, palette=palette,
	               scale="count", inner=inner, gridsize=100, cut=0)
	plt.title('Intervention Effect on Image')
	plt.xlabel('Dimension')
	plt.ylabel('Effect')
	plt.tight_layout()


	return fig, ax


#
# def viz_originals(S, **unused):
#
# 	X = S.X
# 	img_W = S.img_W
#
# 	fig = show_nums(X, figsize=(9, 9), H=img_W)
# 	# fig.suptitle("originals", fontsize=14)
# 	# plt.tight_layout()
# 	border, between = 0.02, 0.01
# 	plt.subplots_adjust(wspace=between, hspace=between,
# 						left=border, right=1 - border, bottom=border, top=1 - border)
#
# 	return fig,
#
# def viz_reconstructions(S, **unused):
#
# 	rec = S.rec
# 	img_W = S.img_W
# 	border, between = S.border, S.between
#
# 	fig = show_nums(rec, figsize=(9, 9), W=img_W)
# 	plt.subplots_adjust(wspace=between, hspace=between,
# 						left=border, right=1 - border, bottom=border, top=1 - border)
#
# 	return fig,
#
# def viz_hybrids(S, **unused):
#
# 	hyb = S.hyb
# 	img_W = S.img_W
# 	border, between = S.border, S.between
#
# 	fig = show_nums(hyb, figsize=(9, 9), W=img_W)
# 	plt.subplots_adjust(wspace=between, hspace=between,
# 						left=border, right=1 - border, bottom=border, top=1 - border)
#
# 	return fig,
#
# def viz_generated(S, **unused):
# 	gen = S.gen
# 	img_W = S.img_W
# 	border, between = S.border, S.between
#
# 	fig = show_nums(gen, figsize=(9, 9), W=img_W)
# 	plt.subplots_adjust(wspace=between, hspace=between,
# 						left=border, right=1 - border, bottom=border, top=1 - border)
#
# 	return fig,
#
# def viz_latent(S, **unused):
# 	# if dis_q is None:
#
# 	assert 'int_q' in S
#
# 	if 'full_q' in S and S.full_q is not None:
# 		int_q = S.full_q
# 		if isinstance(S.full_q, distrib.Distribution):
# 			dis_int_q = S.full_q
# 		else:
# 			dis_int_q = None
#
# 	else:
#
# 		int_q = S.int_q
# 		dis_int_q = S.dis_int_q
#
# 	if dis_int_q is not None:
# 		int_q = int_q.loc
# 		dis_int_q = None
#
# 	# print(int_q.shape)
#
# 	Xs = np.arange(int_q.shape[-1]) + 1
# 	inds = np.stack([Xs] * int_q.shape[0])
#
# 	vals = int_q.cpu().numpy()
# 	df1 = pd.DataFrame({'x': inds.reshape(-1), 'y': vals.reshape(-1)})
#
# 	if dis_int_q is not None:
#
# 		fig, ax = plt.subplots(figsize=(9, 3))
#
# 		df1['moment'] = 'mu'
#
# 		vals = dis_int_q.scale.log().cpu().numpy()
# 		df2 = pd.DataFrame({'x': inds.reshape(-1), 'y': vals.reshape(-1)})
# 		df2['moment'] = 'log(sigma)'
#
# 		df = pd.concat([df1, df2])
#
# 		hue = 'moment'
# 		split = False
# 		color = None
# 		palette = 'muted'
# 		inner = 'box'
#
# 		sns.violinplot(x='x', y='y', hue=hue,
# 					   data=df, split=split, color=color, palette=palette,
# 					   scale="count", inner=inner)
# 		plt.title('Distributions of Latent Dimensions')
# 		plt.xlabel('Dimension')
# 		plt.ylabel('Values')
# 		plt.legend(loc=8)
# 		plt.tight_layout()
#
# 	else:
#
# 		fig, ax = plt.subplots(figsize=(9, 3))
#
# 		df = df1
#
# 		hue = None
# 		split = False
# 		color = 'C0'
# 		inner = 'box'
# 		palette = None
#
# 		sns.violinplot(x='x', y='y', hue=hue,
# 					   data=df, split=split, color=color, palette=palette,
# 					   scale="count", inner=inner, gridsize=100, )
# 		if 'lim_y' in S:
# 			plt.ylim(-S.lim_y, S.lim_y)
# 		plt.title('Distributions of Latent Dimensions')
# 		plt.xlabel('Dimension')
# 		plt.ylabel('Values')
# 		plt.tight_layout()
#
#
# 	return fig,

#
# def viz_interventions(S, **unused):
#
# 	A = S.A
# 	X = S.X
# 	q = S.q
# 	model = S.model
# 	img_W = S.img_W
# 	dataset = S.dataset
#
# 	int_q = S.int_q
#
# 	all_diffs = S.all_diffs
#
#
# 	# Intervention Effect
#
# 	vals = all_diffs.cpu().numpy()
# 	Xs = np.arange(vals.shape[-1]) + 1
# 	inds = np.stack([Xs] * vals.shape[0])
# 	df = pd.DataFrame({'x': inds.reshape(-1), 'y': vals.reshape(-1)})
# 	# df['moment']='log(sigma)'
#
# 	hue = None
# 	split = False
# 	color = 'C2'
# 	inner = 'box'
# 	palette = None
#
# 	fig, ax = plt.subplots(figsize=(9, 3))
# 	sns.violinplot(x='x', y='y', hue=hue,
# 				   data=df, split=split, color=color, palette=palette,
# 				   scale="count", inner=inner, gridsize=100)
# 	plt.title('Intervention Effect on Image')
# 	plt.xlabel('Dimension')
#
# 	plt.ylabel('Effect')
# 	plt.tight_layout()
#
# 	return fig,
#
# def viz_traversals(S, **unused):
#
# 	walks = S.saved_walks
#
# 	anims = [util.Video(walk) for walk in walks]
#
# 	return anims

fid_types = {
	'train': '3dshapes_stats_fid_train.pkl',
	'test': '3dshapes_stats_fid_test.pkl',
	'full': '3dshapes_stats_fid.pkl',
}

def prior_generate(S, **unused):

	model = S.model

	util.set_seed(0)

	def generate(N):
		with torch.no_grad():
			return model.generate(N)

	return generate

def hybrid_generate(S, **unused):

	A = S.A

	Q = S.full_q
	assert Q is not None, 'no latent space'

	if isinstance(Q, distrib.Distribution):
		Q = Q.loc

	model = S.model


	util.set_seed(0)

	def generate(N):

		idx = torch.randperm(len(Q))[:N]

		q = Q[idx].to(A.device)

		with torch.no_grad():
			gen = model.decode(util.shuffle_dim(q)).detach()

		return gen

	return generate

def rec_generate(S, **unused):

	A = S.A

	Q = S.full_q
	assert Q is not None, 'no latent space'

	if isinstance(Q, distrib.Distribution):
		Q = Q.loc

	model = S.model


	util.set_seed(0)

	def generate(N):

		idx = torch.randperm(len(Q))[:N]

		q = Q[idx].to(A.device)

		with torch.no_grad():
			gen = model.decode(q).detach()

		return gen

	return generate

gen_types = {
	'hybrid': hybrid_generate,
	'prior': prior_generate,
	'rec': rec_generate,
}

def make_fid_fn(gen_type='hybrid', fid_type='full'):
	def _top_fn(S, pbar=None, **unused):

		if 'fid_gen_stats' not in S:
			S.fid_gen_stats = {}

		if gen_type not in S.fid_gen_stats:

			model = S.model
			if gen_type != 'prior' and model.enc is None:
				return None


			if 'inception' not in S:
				# print('Loading inception model')
				# start = time.time()
				S.inception = load_inception_model(dim=2048, device=S.A.device)
				# print('... done ({:3.2f})'.format(time.time() - start))

			inception = S.inception

			generate = gen_types[gen_type](S, **unused)

			S.fid_gen_stats[gen_type] = compute_inception_stat(generate, inception=inception, pbar=pbar)

			update_checkpoint(S, 'fid_gen_stats', overwrite=True)


		if 'fid_ref_stat' not in S:
			S.fid_ref_stat = {}

		if fid_type not in S.fid_ref_stat:
			path = os.path.join(os.environ["FOUNDATION_DATA_DIR"], '3dshapes', fid_types[fid_type])
			f = pickle.load(open(path, 'rb'))
			S.fid_ref_stat[fid_type] = f['m'][:], f['sigma'][:]


		stats = S.fid_gen_stats[gen_type]
		ref_stats = S.fid_ref_stat[fid_type]

		return compute_frechet_distance(*stats, *ref_stats)

	return _top_fn


def eval_disentanglement_metric(eval_fn, S, **unused):

	A = S.A
	model = S.model

	if model.enc is None:
		return None

	if 'repr_fn' not in S:
		S.repr_fn = dis_eval.representation_func(model, A.device)
	repr_fn = S.repr_fn

	if 'dis_dataset' not in S:
		S.dis_dataset = dis_eval.shapes3d.Shapes3D()
	dis_dataset = S.dis_dataset

	# start = time.time()
	result = eval_fn(model='', representation_function=repr_fn, dataset=dis_dataset, seed=0)
	# print('Took {:2.2f} s'.format(time.time()-start))

	return result

def make_dis_eval(eval_fn):
	def _eval_metric(S, **unused):
		return eval_disentanglement_metric(eval_fn, S, **unused)
	return _eval_metric




class Hybrid_Controller(train.Run_Manager):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, load_fn=load_fn, run_model_fn=run_model, **kwargs)
						#  eval_fns=OrderedDict({
						#
						# 	 'FID-prior': make_fid_fn('prior', fid_type='full'),
						# 	 'FID-hyb': make_fid_fn('hybrid', fid_type='full'),
						# 	 'FID-rec': make_fid_fn('rec', fid_type='full'),
						#
						# 	 'IRS': make_dis_eval(dis_eval.eval_irs),
						# 	 'MIG': make_dis_eval(dis_eval.eval_mig),
						# 	 'DCI': make_dis_eval(dis_eval.eval_dci),
						#
						# 	 'SAP': make_dis_eval(dis_eval.eval_sap),
						# 	 'ModExp': make_dis_eval(dis_eval.eval_modularity_explicitness),
						# 	 'Unsup': make_dis_eval(dis_eval.eval_unsupervised),
						#
						# 	 # 'bVAE': make_dis_eval(dis_eval.eval_beta_vae),
						# 	 # 'FVAE': make_dis_eval(dis_eval.eval_factor_vae),
						#  }),
						#  viz_fns=OrderedDict({
						# 	'original': viz_originals,
						# 	'recs': viz_reconstructions,
						# 	'gens': viz_generated,
						# 	'hybrid': viz_hybrids,
						# 	'latent': viz_latent,
						# 	'effects': viz_interventions,
						# 	'traversals': viz_traversals,
						# }), **kwargs)


