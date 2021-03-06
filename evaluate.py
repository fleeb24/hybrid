import os
import argparse
import numpy as np

from foundation import train
from foundation import models
# from hybrid import get_model

import torch
import torch.nn as nn

from metrics import mig
from metrics import metric_factor_vae
from metrics import metric_beta_vae
from metrics import modularity_explicitness
from metrics import dci, irs, sap
from metrics import unsupervised_metrics
from ground_truth import dsprites, shapes3d



def parse_args():
	parser = argparse.ArgumentParser(description='Evaluate Disentanglement Metrics')
	parser.add_argument('--model', type=str, required = True, metavar='M',
                    help='path to saved model files to be evaluated')
	parser.add_argument('--save_path', type=str, default = None, metavar='N',
                    help='Path to save result files')
	parser.add_argument('--all', action='store_true', default=True,
                    help='Evaluate all Metrics')
	parser.add_argument('--seed', type=int, default=10, metavar='S',
                    help='random seed (default: 10)')
	return parser.parse_args()

def _load_model_eval(model_path, args):
	return train.load(path=model_path, A=args, get_model=get_model, get_data = None, return_args = True)

def _save_to_text(save_path, results):
	with open(os.path.join(save_path,'results.txt'),'w') as t:
		for i in ALL_METRICS:
			t.write(i + ':\n')
			for j in results[i].keys():
				t.write('\t'+ j + ': '+ str(results[i][j]) + '\n')
			t.write('\n\n')
	return 0

def _assert_args_and_load(model, args, dataset, representation_function):
	if not isinstance(model, nn.Module)  and not isinstance(model,str):
		raise Exception('Should provide the model or the path to the model, but got {}'.format(model.__class__.__name__))
	if isinstance(model, str) and representation_function is None:
		args, model = _load_model_eval(model, args)
		representation_function = representation_func(model, args['device'])
		if args.dataset.name == 'dspr':
			dataset = dsprites.DSprites()
		elif args.dataset.name == '3dshapes':
			dataset = shapes3d.Shapes3D()
		else:
			raise Exception('Dataset not implemented yet')
	return representation_function, dataset
		


class representation_func(object):
	def __init__(self, model, device):
		self.model = model.enc
		self.model.eval()
		self.device = device
	def __call__(self, x):
		#x = np.transpose(x, (0,3,1,2))
		"""Computes representation vector for input images."""
		#output = self.model(torch.Tensor(x).to(self.device))
		output = self.model(x.to(self.device))
		if isinstance(output, torch.distributions.normal.Normal):
			output = output.loc
		return output.detach().cpu().numpy()

def eval_all(model = None, args = None, dataset = None, representation_function = None, seed = 10, save_path = None):
	representation_function, dataset = _assert_args_and_load(model, args, dataset, representation_function)
	results = {}
	for id_, metric in enumerate(ALL_METRICS):
		print('Evaluating {}'.format(metric))
		results[metric] =  ALL_METRIC_FUNC[id_](model, args, dataset, representation_function, seed)

	if save_path is not None:
		return _save_to_text(save_path, results)
	else:
		return results
		

def eval_beta_vae(model = None, args = None, dataset = None, representation_function = None, seed = 10):
	representation_function, dataset = _assert_args_and_load(model, args, dataset, representation_function)
	np.random.seed(seed) 
	return metric_beta_vae.compute_beta_vae_sklearn(dataset, representation_function, np.random, 64, 10000, 5000)

def eval_factor_vae(model = None, args = None, dataset = None, representation_function = None, seed = 10):
	representation_function, dataset = _assert_args_and_load(model, args, dataset, representation_function)
	np.random.seed(seed) 
	return metric_factor_vae.compute_factor_vae(dataset, representation_function, np.random, 64, 10000, 5000, 10000)

def eval_mig(model = None, args = None, dataset = None, representation_function = None, seed = 10):
	representation_function, dataset = _assert_args_and_load(model, args, dataset, representation_function)
	np.random.seed(seed) 
	return mig.compute_mig(dataset, representation_function, np.random, 10000)


def eval_dci(model = None, args = None, dataset = None, representation_function = None, seed = 10):
	representation_function, dataset = _assert_args_and_load(model, args, dataset, representation_function)
	np.random.seed(seed) 
	return dci.compute_dci(dataset, representation_function, np.random, 10000, 5000)

def eval_irs(model = None, args = None, dataset = None, representation_function = None, seed = 10):
	representation_function, dataset = _assert_args_and_load(model, args, dataset, representation_function)
	np.random.seed(seed) 
	return irs.compute_irs(dataset, representation_function, np.random)

def eval_sap(model = None, args = None, dataset = None, representation_function = None, seed = 10):
	representation_function, dataset = _assert_args_and_load(model, args, dataset, representation_function)
	np.random.seed(seed) 
	return sap.compute_sap(dataset, representation_function, np.random, 10000, 5000, False)

def eval_modularity_explicitness(model = None, args = None, dataset = None, representation_function = None, seed = 10):
	representation_function, dataset = _assert_args_and_load(model, args, dataset, representation_function)
	np.random.seed(seed) 
	return modularity_explicitness.compute_modularity_explicitness(dataset, representation_function, np.random, 10000, 5000, 64)

def eval_unsupervised(model = None, args = None, dataset = None, representation_function = None, seed = 10):
	representation_function, dataset = _assert_args_and_load(model, args, dataset, representation_function)
	np.random.seed(seed) 
	return unsupervised_metrics.unsupervised_metrics(dataset, representation_function, np.random, 10000)

ALL_METRICS = ['Beta VAE Score', 'Factor VAE Score', 'MIG', 'DCI', 'IRS', 'SAP', 'Modularity Explicitness', 'Unsupervised Metrics']#, 'FID'] #TODO: ADD Fid score
ALL_METRIC_FUNC = [eval_beta_vae, eval_factor_vae, eval_mig, eval_dci, eval_irs, eval_sap, eval_modularity_explicitness, eval_unsupervised]

if __name__ == '__main__':
	args = parse_args()
	if args.save_path is None:
		args.save_path = args.model


	
	if args.all:
		eval_all(model = args.model, seed = args.seed, save_path = args.save_path)
