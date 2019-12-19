# Author: Chris Moody <chrisemoody@gmail.com>
# License: MIT

# This simple example loads the newsgroups data from sklearn
# and train an LDA-like model on it
import os
import os.path
import pickle
import time
import shelve

import chainer
from chainer import cuda
from chainer import serializers
import chainer.optimizers as O
import numpy as np

from lda2vec import utils
from lda2vec import prepare_topics, print_top_words_per_topic, topic_coherence
from examples.twenty_newsgroups.lda2vec.lda2vec_model import LDA2Vec

latest = 0
hdf_files = [f for f in os.listdir(os.getcwd()) if '.hdf5' in f and 'lda2vec' in f]
print(hdf_files)
nums = [int(f[7:10]) for f in hdf_files]
if nums:
    latest = max(nums)
print(nums)
print(latest)

# gpu_id = int(os.getenv('CUDA_GPU', 0))  # todo: get computer with gpu
# cuda.get_device(gpu_id).use()
# print ("Using GPU " + str(gpu_id))

data_dir = os.getenv('data_dir', '../data/')
fn_vocab = '{data_dir:s}/vocab.pkl'.format(data_dir=data_dir)
fn_corpus = '{data_dir:s}/corpus.pkl'.format(data_dir=data_dir)
fn_flatnd = '{data_dir:s}/flattened.npy'.format(data_dir=data_dir)
fn_docids = '{data_dir:s}/doc_ids.npy'.format(data_dir=data_dir)
fn_vectors = '{data_dir:s}/vectors.npy'.format(data_dir=data_dir)
vocab = pickle.load(open(fn_vocab, 'rb'))
corpus = pickle.load(open(fn_corpus, 'rb'))
flattened = np.load(fn_flatnd).astype(np.int32)  # todo: adjust preprocessor to not need to cast this
doc_ids = np.load(fn_docids).astype(np.int32)  # todo: adjust preprocessor to not need to cast this
vectors = np.load(fn_vectors)

# Model Parameters
# Number of documents
n_docs = int(doc_ids.max() + 1)
# Number of unique words in the vocabulary
n_vocab = int(flattened.max() + 1)
# 'Strength' of the dircihlet prior; 200.0 seems to work well
clambda = 200.0
# Number of topics to fit
n_topics = int(os.getenv('n_topics', 20))
batchsize = 2 ** 15
# Power for neg sampling
power = float(os.getenv('power', 0.75))
# Intialize with pretrained word vectors
pretrained = bool(int(os.getenv('pretrained', True)))
# Sampling temperature
temperature = float(os.getenv('temperature', 1.0))
# Number of dimensions in a single word vector
n_units = int(os.getenv('n_units', 300))
# Get the string representation for every compact key
words = corpus.word_list(vocab)[:n_vocab]
# How many tokens are in each document
doc_idx, lengths = np.unique(doc_ids, return_counts=True)
doc_lengths = np.zeros(doc_ids.max() + 1, dtype='int32')
doc_lengths[doc_idx] = lengths
# Count all token frequencies
tok_idx, freq = np.unique(flattened, return_counts=True)
tok_idx = tok_idx.astype(np.int32)
freq = freq.astype(np.int32)
term_frequency = np.zeros(n_vocab, dtype='int32')
term_frequency[tok_idx] = freq

for key in sorted(locals().keys()):
    val = locals()[key]
    if len(str(val)) < 100 and '<' not in str(val):
        print (key, val)

model = LDA2Vec(n_documents=n_docs, n_document_topics=n_topics,
                n_units=n_units, n_vocab=n_vocab, counts=term_frequency,
                n_samples=15, power=power, temperature=temperature)

if os.path.exists('lda2vec%3d.hdf5' % latest):
    print ("Reloading from saved")
    serializers.load_hdf5("lda2vec%3d.hdf5" % latest, model)
if pretrained:
    model.sampler.W.data[:, :] = vectors[:n_vocab, :]
# model.to_gpu()
optimizer = O.Adam(final_lr=0.0001)
optimizer.setup(model)
clip = chainer.optimizer.GradientClipping(5.0)
optimizer.add_hook(clip)

j = 0
fraction = batchsize * 1.0 / flattened.shape[0]
progress = shelve.open('progress.shelve')
steps = flattened.shape[0] // batchsize
print('steps per epoch: %d' % steps)
num_epochs = 25
for epoch in range(num_epochs):
    data = prepare_topics(cuda.to_cpu(model.mixture.weights.W.data).copy(),
                          cuda.to_cpu(model.mixture.factors.W.data).copy(),
                          cuda.to_cpu(model.sampler.W.data).copy(),
                          words)
    top_words = print_top_words_per_topic(data)
    if j % 100 == 0 and j > 100:
        coherence = topic_coherence(top_words)
        for j in range(n_topics):
            print(j, coherence[(j, 'cv')])
        kw = dict(top_words=top_words, coherence=coherence, epoch=epoch)
        progress[str(epoch)] = pickle.dumps(kw)
    data['doc_lengths'] = doc_lengths
    data['term_frequency'] = term_frequency
    np.savez('topics.pyldavis', **data)
    for d, f in utils.chunks(batchsize, doc_ids, flattened):
        t0 = time.time()
        model.cleargrads()
        l = model.fit_partial(d.copy(), f.copy())
        prior = model.prior()
        loss = prior * fraction
        loss.backward()
        optimizer.update()
        msg = ("J:{j:05d} E:{epoch:05d} L:{loss:1.3e} "
               "P:{prior:1.3e} R:{rate:1.3e}")
        prior.to_cpu()
        loss.to_cpu()
        t1 = time.time()
        dt = t1 - t0
        rate = batchsize / dt
        logs = dict(loss=float(l), epoch=epoch+latest, j=j,
                    prior=float(prior.data), rate=rate)
        print(msg.format(**logs))
        j += 1
    if (epoch+latest+1) % 5 == 0:
        serializers.save_hdf5("lda2vec%3d.hdf5" % (latest+epoch+1), model)
    j = j % steps
