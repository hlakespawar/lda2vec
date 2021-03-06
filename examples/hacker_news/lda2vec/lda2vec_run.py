# Author: Chris Moody <chrisemoody@gmail.com>
# License: MIT

# This simple example loads the newsgroups data from sklearn
# and train an LDA-like model on it
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
from lda2vec import prepare_topics, print_top_words_per_topic
from examples.hacker_news.lda2vec.lda2vec_model import LDA2Vec

latest = 0
hdf_files = [f for f in os.listdir(os.getcwd()) if '.hdf5' in f and 'lda2vec' in f]
nums = [int(f[7:10]) for f in hdf_files]
if nums:
    latest = max(nums)

# gpu_id = int(os.getenv('CUDA_GPU', 0))
# cuda.get_device(gpu_id).use()
# print ("Using GPU " + str(gpu_id))

# You must run preprocess.py before this data becomes available
vocab = pickle.load(open('../data/vocab', 'rb'))
corpus = pickle.load(open('../data/corpus', 'rb'))
data = np.load(open('../data/data.npz', 'rb'))
flattened = data['flattened'].astype('int32')
story_id = data['story_id'].astype('int32')
author_id = data['author_id'].astype('int32')
time_id = data['time_id'].astype('int32')
ranking = data['ranking'].astype('float32')
score = data['score'].astype('float32')


# Model Parameters
# Number of documents
n_stories = int(story_id.max() + 1)
# Number of users
n_authors = int(author_id.max() + 1)
# Number of unique words in the vocabulary
n_vocab = int(flattened.max() + 1)
# Number of dimensions in a single word vector
n_units = 256
# Number of topics to fit
n_story_topics = 40
n_author_topics = 20
batchsize = 2**12
# Get the string representation for every compact key
words = corpus.word_list(vocab)[:n_vocab]

# How many tokens are in each story
sty_idx, lengths = np.unique(story_id, return_counts=True)
sty_len = np.zeros(sty_idx.max() + 1, dtype='int32')
sty_len[sty_idx] = lengths

# How many tokens are in each author
aut_idx, lengths = np.unique(author_id, return_counts=True)
aut_len = np.zeros(aut_idx.max() + 1, dtype='int32')
aut_len[aut_idx] = lengths

# Count all token frequencies
tok_idx, freq = np.unique(flattened, return_counts=True)
term_frequency = np.zeros(n_vocab, dtype='int32')
term_frequency[tok_idx] = freq

model = LDA2Vec(n_stories=n_stories, n_story_topics=n_story_topics,
                n_authors=n_authors, n_author_topics=n_author_topics,
                n_units=n_units, n_vocab=n_vocab, counts=term_frequency,
                n_samples=15)

if os.path.exists('lda2vec%3d.hdf5' % latest):
    print("Reloading from saved")
    serializers.load_hdf5("lda2vec%3d.hdf5" % latest, model)
# model.to_gpu()
optimizer = O.Adam()
optimizer.setup(model)
clip = chainer.optimizer.GradientClipping(5.0)
optimizer.add_hook(clip)

j = 0
epoch = 0
fraction = batchsize * 1.0 / flattened.shape[0]
progress = shelve.open('progress.shelve')
steps = flattened.shape[0] // batchsize
print('steps per epoch: %d' % steps)
for epoch in range(5000):
    ts = prepare_topics(model.mixture_sty.weights.W.data,
                        model.mixture_sty.factors.W.data,
                        model.sampler.W.data,
                        words)

    print_top_words_per_topic(ts)
    ts['doc_lengths'] = sty_len
    ts['term_frequency'] = term_frequency
    np.savez('topics.story.pyldavis', **ts)
    ta = prepare_topics(model.mixture_aut.weights.W.data,
                        model.mixture_aut.factors.W.data,
                        model.sampler.W.data,
                        words)

    print_top_words_per_topic(ta)
    ta['doc_lengths'] = aut_len
    ta['term_frequency'] = term_frequency
    np.savez('topics.author.pyldavis', **ta)
    for s, a, f in utils.chunks(batchsize, story_id, author_id, flattened):
        t0 = time.time()
        model.cleargrads()
        l = model.fit_partial(s.copy(), a.copy(), f.copy())
        prior = model.prior()
        loss = prior * fraction
        loss.backward()
        optimizer.update()
        msg = ("J:{j:05d} E:{epoch:05d} L:{loss:1.3e} "
               "P:{prior:1.3e} R:{rate:1.3e}")

        t1 = time.time()
        dt = t1 - t0
        rate = batchsize / dt
        logs = dict(loss=float(l), epoch=epoch, j=j,
                    prior=float(prior.data), rate=rate)
        print(msg.format(**logs))
        j += 1
    if epoch % 5 == 0:
        serializers.save_hdf5('lda2vec%3d.hdf5' % (latest+epoch+1), model)
    j = j % steps
