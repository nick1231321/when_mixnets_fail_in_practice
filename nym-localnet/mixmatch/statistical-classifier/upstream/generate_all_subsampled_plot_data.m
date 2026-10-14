exp_ids = {};
exp_ids{1} = 'baseline';
exp_ids{2} = 'no-cover';
% exp_ids{1} = 'low-delay';
% exp_ids{2} = 'high-delay';
% exp_ids{3} = 'live-nym';
exp_database_dir = '../datasets';
exp_pieces = {};
exp_pieces{1} = 20;
exp_pieces{2} = 20;
% exp_pieces{1} = 16;
% exp_pieces{2} = 16;
% exp_pieces{3} = 46;


for k=1:length(exp_ids)
  [Pmisses, Pfas] = process_real_data_alt_delay_characteristic_experiment_results(exp_database_dir, exp_ids{k}, exp_pieces{k});
  subsampled_TPR = {};
  subsampled_FPR = {};
  for l=1:length(Pmisses)
    [subsampled_TPR{l}, subsampled_FPR{l}] = subsample_TPR_FPR(Pfas{l}, 1-Pmisses{l}, 500);
  endfor
  clear Pfas
  clear Pmisses
  for l=1:(length(subsampled_TPR)-1)
    fid = fopen([exp_database_dir '/' exp_ids{k} '/subsampled_ROC_' num2str(100*l) '.txt'], 'w');
    for m=1:500
      fprintf(fid, '%f %f\n', subsampled_TPR{l}(m), subsampled_FPR{l}(m));
    endfor
    fclose(fid);
  endfor
  fid = fopen([exp_database_dir '/' exp_ids{k} '/subsampled_ROC_full.txt'], 'w');
  for m=1:500
    fprintf(fid, '%f %f\n', subsampled_TPR{length(subsampled_TPR)}(m), subsampled_FPR{length(subsampled_FPR)}(m));
  endfor
  fclose(fid);
endfor
